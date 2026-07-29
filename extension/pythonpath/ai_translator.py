# -*- coding: utf-8 -*-
"""LibreOffice AI Translator extension.

The module intentionally depends only on Python's standard library and UNO so it
can run with the Python interpreter bundled with LibreOffice.
"""

import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path

import uno
import unohelper
from com.sun.star.awt import XActionListener
from com.sun.star.frame import XDispatch, XDispatchProvider
from com.sun.star.lang import XServiceInfo

IMPLEMENTATION_NAME = "org.baimard.libreoffice.ai.translator.Handler"
SERVICE_NAMES = ("com.sun.star.frame.ProtocolHandler",)
PROTOCOL = "org.baimard.libreoffice.ai.translator:"
DEFAULT_CONFIG = {
    "api_key": "",
    "api_url": "https://api.openai.com/v1/responses",
    "model": "gpt-5-mini",
    "source_language": "Auto",
    "target_language": "French",
    "mode": "replace",
    "max_chars": 9000,
}


def _property(name, value):
    prop = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
    prop.Name = name
    prop.Value = value
    return prop


class TranslatorError(RuntimeError):
    pass


class ConfigStore:
    def __init__(self, ctx):
        self.ctx = ctx

    def _path(self):
        provider = self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.util.PathSubstitution", self.ctx
        )
        user_url = provider.substituteVariables("$(user)", True)
        user_path = Path(uno.fileUrlToSystemPath(user_url))
        return user_path / "ai-translator" / "config.json"

    def load(self):
        config = dict(DEFAULT_CONFIG)
        path = self._path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    config.update({k: v for k, v in data.items() if k in config})
            except (OSError, ValueError):
                pass
        return config

    def save(self, config):
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)


class OpenAITranslator:
    def __init__(self, config):
        self.config = config

    def translate(self, text):
        if not text or not text.strip():
            return text
        api_key = str(self.config.get("api_key", "")).strip()
        if not api_key:
            raise TranslatorError("No OpenAI API key is configured.")

        source = self.config.get("source_language") or "Auto"
        target = self.config.get("target_language") or "French"
        instruction = (
            "Translate the supplied LibreOffice document text faithfully. "
            "Preserve paragraphs, line breaks, list markers, numbers, URLs, email addresses, "
            "placeholders and punctuation. Do not summarize, explain, annotate or add markdown. "
            "Return only the translated text. "
            f"Source language: {source}. Target language: {target}."
        )
        payload = {
            "model": self.config.get("model") or DEFAULT_CONFIG["model"],
            "instructions": instruction,
            "input": text,
        }
        request = urllib.request.Request(
            str(self.config.get("api_url") or DEFAULT_CONFIG["api_url"]),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "User-Agent": "LibreOffice-AI-Translator/0.1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120, context=ssl.create_default_context()) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
                message = detail.get("error", {}).get("message") or str(detail)
            except Exception:
                message = str(exc)
            raise TranslatorError(f"OpenAI API error ({exc.code}): {message}") from exc
        except urllib.error.URLError as exc:
            raise TranslatorError(f"Network error: {exc.reason}") from exc
        except (ValueError, OSError) as exc:
            raise TranslatorError(f"Invalid API response: {exc}") from exc

        output = self._extract_output(result)
        if output is None:
            raise TranslatorError("The OpenAI API returned no translated text.")
        return output

    @staticmethod
    def _extract_output(result):
        direct = result.get("output_text")
        if isinstance(direct, str):
            return direct
        parts = []
        for item in result.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts) if parts else None


class DialogActionListener(unohelper.Base, XActionListener):
    def __init__(self, callback):
        self.callback = callback

    def actionPerformed(self, event):
        self.callback(event)

    def disposing(self, event):
        pass


class ExtensionHandler(unohelper.Base, XDispatchProvider, XDispatch, XServiceInfo):
    def __init__(self, ctx):
        self.ctx = ctx
        self.smgr = ctx.ServiceManager
        self.desktop = self.smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        self.store = ConfigStore(ctx)

    def getImplementationName(self):
        return IMPLEMENTATION_NAME

    def supportsService(self, service_name):
        return service_name in SERVICE_NAMES

    def getSupportedServiceNames(self):
        return SERVICE_NAMES

    def queryDispatch(self, url, target_frame_name, search_flags):
        return self if url.Protocol == PROTOCOL else None

    def queryDispatches(self, descriptors):
        return tuple(self.queryDispatch(d.FeatureURL, d.FrameName, d.SearchFlags) for d in descriptors)

    def dispatch(self, url, arguments):
        command = url.Path
        try:
            if command == "configure":
                self._configure()
            elif command == "translate-selection":
                self._translate_selection()
            elif command == "translate-document":
                self._translate_document()
        except Exception as exc:
            self._message("LibreOffice AI Translator", str(exc), error=True)

    def addStatusListener(self, listener, url):
        pass

    def removeStatusListener(self, listener, url):
        pass

    def _document(self):
        document = self.desktop.getCurrentComponent()
        if not document or not document.supportsService("com.sun.star.text.TextDocument"):
            raise TranslatorError("Open a LibreOffice Writer document first.")
        return document

    def _translate_selection(self):
        document = self._document()
        selection = document.CurrentController.getSelection()
        ranges = []
        if hasattr(selection, "getCount"):
            for index in range(selection.getCount()):
                item = selection.getByIndex(index)
                if hasattr(item, "getString"):
                    ranges.append(item)
        elif hasattr(selection, "getString"):
            ranges.append(selection)
        ranges = [item for item in ranges if item.getString().strip()]
        if not ranges:
            raise TranslatorError("Select some text to translate.")

        config = self.store.load()
        translator = OpenAITranslator(config)
        document.lockControllers()
        try:
            for text_range in ranges:
                original = text_range.getString()
                translated = self._translate_in_chunks(translator, original, int(config.get("max_chars", 9000)))
                if config.get("mode") == "append":
                    text_range.setString(original + "\n" + translated)
                else:
                    text_range.setString(translated)
        finally:
            document.unlockControllers()
        document.setModified(True)
        self._message("LibreOffice AI Translator", "Translation completed.")

    def _translate_document(self):
        document = self._document()
        config = self.store.load()
        translator = OpenAITranslator(config)
        paragraphs = []
        enumeration = document.Text.createEnumeration()
        while enumeration.hasMoreElements():
            element = enumeration.nextElement()
            if element.supportsService("com.sun.star.text.Paragraph") and element.getString().strip():
                paragraphs.append(element)
        if not paragraphs:
            raise TranslatorError("The document contains no translatable paragraphs.")

        document.lockControllers()
        try:
            total = len(paragraphs)
            for index, paragraph in enumerate(paragraphs, 1):
                self._set_status(document, f"Translating paragraph {index}/{total}…")
                original = paragraph.getString()
                translated = self._translate_in_chunks(translator, original, int(config.get("max_chars", 9000)))
                if config.get("mode") == "append":
                    paragraph.setString(original + "\n" + translated)
                else:
                    paragraph.setString(translated)
        finally:
            self._set_status(document, "")
            document.unlockControllers()
        document.setModified(True)
        self._message("LibreOffice AI Translator", f"Translated {len(paragraphs)} paragraphs.")

    @staticmethod
    def _translate_in_chunks(translator, text, max_chars):
        max_chars = max(1000, min(max_chars, 30000))
        if len(text) <= max_chars:
            return translator.translate(text)
        chunks = []
        remaining = text
        while len(remaining) > max_chars:
            cut = remaining.rfind("\n", 0, max_chars)
            if cut < max_chars // 2:
                cut = remaining.rfind(". ", 0, max_chars)
                if cut >= max_chars // 2:
                    cut += 1
            if cut < max_chars // 2:
                cut = max_chars
            chunks.append(remaining[:cut])
            remaining = remaining[cut:]
        if remaining:
            chunks.append(remaining)
        return "".join(translator.translate(chunk) for chunk in chunks)

    def _configure(self):
        config = self.store.load()
        model = self.smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialogModel", self.ctx)
        model.Width = 230
        model.Height = 174
        model.Title = "LibreOffice AI Translator"

        self._add_label(model, "apiLabel", 8, 8, 60, "OpenAI API key")
        self._add_edit(model, "apiKey", 72, 6, 150, config.get("api_key", ""), password=True)
        self._add_label(model, "urlLabel", 8, 30, 60, "API URL")
        self._add_edit(model, "apiUrl", 72, 28, 150, config.get("api_url", ""))
        self._add_label(model, "modelLabel", 8, 52, 60, "Model")
        self._add_edit(model, "model", 72, 50, 150, config.get("model", ""))
        self._add_label(model, "sourceLabel", 8, 74, 60, "Source language")
        self._add_edit(model, "source", 72, 72, 150, config.get("source_language", "Auto"))
        self._add_label(model, "targetLabel", 8, 96, 60, "Target language")
        self._add_edit(model, "target", 72, 94, 150, config.get("target_language", "French"))
        self._add_label(model, "modeLabel", 8, 118, 60, "Output mode")
        self._add_list(model, "mode", 72, 116, 150, ("Replace original", "Append translation"), 1 if config.get("mode") == "append" else 0)
        self._add_button(model, "save", 116, 146, 50, "Save")
        self._add_button(model, "cancel", 172, 146, 50, "Cancel")

        dialog = self.smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialog", self.ctx)
        dialog.setModel(model)
        toolkit = self.smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", self.ctx)
        dialog.createPeer(toolkit, None)

        def on_action(event):
            if event.Source.Model.Name == "save":
                new_config = dict(config)
                new_config.update({
                    "api_key": dialog.getControl("apiKey").getText().strip(),
                    "api_url": dialog.getControl("apiUrl").getText().strip(),
                    "model": dialog.getControl("model").getText().strip(),
                    "source_language": dialog.getControl("source").getText().strip() or "Auto",
                    "target_language": dialog.getControl("target").getText().strip() or "French",
                    "mode": "append" if dialog.getControl("mode").getSelectedItemPos() == 1 else "replace",
                })
                self.store.save(new_config)
                dialog.endExecute()
                self._message("LibreOffice AI Translator", "Configuration saved.")
            else:
                dialog.endExecute()

        listener = DialogActionListener(on_action)
        dialog.getControl("save").addActionListener(listener)
        dialog.getControl("cancel").addActionListener(listener)
        dialog.execute()
        dialog.dispose()

    @staticmethod
    def _add_label(model, name, x, y, width, label):
        control = model.createInstance("com.sun.star.awt.UnoControlFixedTextModel")
        control.Name, control.PositionX, control.PositionY = name, x, y
        control.Width, control.Height, control.Label = width, 12, label
        model.insertByName(name, control)

    @staticmethod
    def _add_edit(model, name, x, y, width, text, password=False):
        control = model.createInstance("com.sun.star.awt.UnoControlEditModel")
        control.Name, control.PositionX, control.PositionY = name, x, y
        control.Width, control.Height, control.Text = width, 14, text
        if password:
            control.EchoChar = ord("•")
        model.insertByName(name, control)

    @staticmethod
    def _add_list(model, name, x, y, width, items, selected):
        control = model.createInstance("com.sun.star.awt.UnoControlListBoxModel")
        control.Name, control.PositionX, control.PositionY = name, x, y
        control.Width, control.Height, control.StringItemList = width, 14, items
        control.SelectedItems = (selected,)
        control.Dropdown = True
        model.insertByName(name, control)

    @staticmethod
    def _add_button(model, name, x, y, width, label):
        control = model.createInstance("com.sun.star.awt.UnoControlButtonModel")
        control.Name, control.PositionX, control.PositionY = name, x, y
        control.Width, control.Height, control.Label = width, 16, label
        model.insertByName(name, control)

    def _message(self, title, message, error=False):
        toolkit = self.smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", self.ctx)
        box_type = uno.getConstantByName(
            "com.sun.star.awt.MessageBoxType.ERRORBOX" if error else "com.sun.star.awt.MessageBoxType.INFOBOX"
        )
        buttons = uno.getConstantByName("com.sun.star.awt.MessageBoxButtons.BUTTONS_OK")
        parent = self.desktop.getCurrentFrame().getContainerWindow() if self.desktop.getCurrentFrame() else None
        box = toolkit.createMessageBox(parent, box_type, buttons, title, message)
        box.execute()

    @staticmethod
    def _set_status(document, message):
        try:
            indicator = document.CurrentController.Frame.createStatusIndicator()
            if message:
                indicator.start(message, 100)
                indicator.setValue(50)
            else:
                indicator.end()
        except Exception:
            pass


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(ExtensionHandler, IMPLEMENTATION_NAME, SERVICE_NAMES)
