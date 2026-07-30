# -*- coding: utf-8 -*-
"""LibreOffice AI Translator extension."""

import json
import os
import ssl
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import uno
import unohelper
from com.sun.star.awt import XActionListener, XCallback
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


class TranslatorError(RuntimeError):
    pass


class ConfigStore:
    def __init__(self, ctx):
        self.ctx = ctx

    def user_path(self):
        provider = self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.util.PathSubstitution", self.ctx
        )
        user_url = provider.substituteVariables("$(user)", True)
        return Path(uno.fileUrlToSystemPath(user_url)) / "ai-translator"

    def config_path(self):
        return self.user_path() / "config.json"

    def log_path(self):
        return self.user_path() / "extension.log"

    def load(self):
        config = dict(DEFAULT_CONFIG)
        path = self.config_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    config.update({key: value for key, value in data.items() if key in config})
            except (OSError, ValueError):
                pass
        return config

    def save(self, config):
        path = self.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)

    def log(self, message):
        try:
            path = self.log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                timestamp = datetime.now().isoformat(timespec="seconds")
                handle.write(f"[{timestamp}] {message}\n")
        except BaseException:
            pass


class OpenAITranslator:
    def __init__(self, config):
        self.config = config

    def translate(self, text):
        if not text or not text.strip():
            return text

        api_key = str(self.config.get("api_key", "")).strip()
        if not api_key:
            raise TranslatorError("Aucune clé API OpenAI n'est configurée.")

        source = self.config.get("source_language") or "Auto"
        target = self.config.get("target_language") or "French"
        instructions = (
            "Translate the supplied LibreOffice document text faithfully. "
            "Preserve paragraphs, line breaks, list markers, numbers, URLs, email addresses, "
            "placeholders and punctuation. Do not summarize, explain, annotate or add markdown. "
            "Return only the translated text. "
            f"Source language: {source}. Target language: {target}."
        )
        payload = {
            "model": self.config.get("model") or DEFAULT_CONFIG["model"],
            "instructions": instructions,
            "input": text,
        }
        request = urllib.request.Request(
            str(self.config.get("api_url") or DEFAULT_CONFIG["api_url"]),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "User-Agent": "LibreOffice-AI-Translator/0.1.3",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                request, timeout=120, context=ssl.create_default_context()
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
                message = detail.get("error", {}).get("message") or str(detail)
            except BaseException:
                message = str(exc)
            raise TranslatorError(f"Erreur de l'API OpenAI ({exc.code}) : {message}") from exc
        except urllib.error.URLError as exc:
            raise TranslatorError(f"Erreur réseau : {exc.reason}") from exc
        except (ValueError, OSError) as exc:
            raise TranslatorError(f"Réponse API invalide : {exc}") from exc

        output = self._extract_output(result)
        if output is None:
            raise TranslatorError("L'API OpenAI n'a retourné aucun texte traduit.")
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


class AsyncCommand(unohelper.Base, XCallback):
    """Execute a command after XDispatch.dispatch() has returned to LibreOffice."""

    def __init__(self, owner, command):
        self.owner = owner
        self.command = command

    def notify(self, data):
        try:
            self.owner._run_command(self.command)
        except BaseException:
            self.owner.store.log(
                "Unhandled asynchronous exception:\n" + traceback.format_exc()
            )
        finally:
            try:
                self.owner._pending.remove(self)
            except BaseException:
                pass


class ExtensionHandler(unohelper.Base, XDispatchProvider, XDispatch, XServiceInfo):
    def __init__(self, ctx):
        self.ctx = ctx
        self.smgr = ctx.ServiceManager
        self.desktop = self.smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        self.store = ConfigStore(ctx)
        self._pending = []

    def getImplementationName(self):
        return IMPLEMENTATION_NAME

    def supportsService(self, service_name):
        return service_name in SERVICE_NAMES

    def getSupportedServiceNames(self):
        return SERVICE_NAMES

    def queryDispatch(self, url, target_frame_name, search_flags):
        return self if url.Protocol == PROTOCOL else None

    def queryDispatches(self, descriptors):
        return tuple(
            self.queryDispatch(item.FeatureURL, item.FrameName, item.SearchFlags)
            for item in descriptors
        )

    def dispatch(self, url, arguments):
        command = str(url.Path)
        self.store.log(f"dispatch received: {command}")
        try:
            callback = AsyncCommand(self, command)
            self._pending.append(callback)
            async_service = self.smgr.createInstanceWithContext(
                "com.sun.star.awt.AsyncCallback", self.ctx
            )
            async_service.addCallback(callback, None)
            self.store.log(f"dispatch scheduled: {command}")
        except BaseException:
            self.store.log("dispatch scheduling failed:\n" + traceback.format_exc())

    def _run_command(self, command):
        self.store.log(f"command started: {command}")
        try:
            if command == "configure":
                self._configure()
            elif command == "translate-selection":
                self._translate_selection()
            elif command == "translate-document":
                self._translate_document()
            self.store.log(f"command completed: {command}")
        except BaseException as exc:
            self.store.log(
                f"command failed: {command}: {exc!r}\n" + traceback.format_exc()
            )
            try:
                self._message("Traducteur IA LibreOffice", str(exc), error=True)
            except BaseException:
                self.store.log("error dialog failed:\n" + traceback.format_exc())

    def addStatusListener(self, listener, url):
        pass

    def removeStatusListener(self, listener, url):
        pass

    def _document(self):
        self.store.log("getting current document")
        document = self.desktop.getCurrentComponent()
        if not document or not document.supportsService("com.sun.star.text.TextDocument"):
            raise TranslatorError("Ouvrez d'abord un document LibreOffice Writer.")
        return document

    def _translate_selection(self):
        document = self._document()
        controller = document.getCurrentController()
        selection = controller.getSelection()
        ranges = []

        if hasattr(selection, "getCount"):
            for index in range(selection.getCount()):
                item = selection.getByIndex(index)
                if hasattr(item, "getString") and item.getString().strip():
                    ranges.append(item)
        elif hasattr(selection, "getString") and selection.getString().strip():
            ranges.append(selection)

        self.store.log(f"selection ranges: {len(ranges)}")
        if not ranges:
            raise TranslatorError("Sélectionnez le texte à traduire.")

        config = self.store.load()
        translator = OpenAITranslator(config)
        replacements = []
        for text_range in ranges:
            original = text_range.getString()
            self.store.log(f"translating selection: {len(original)} chars")
            translated = self._translate_in_chunks(
                translator, original, int(config.get("max_chars", 9000))
            )
            replacement = (
                original + "\n" + translated
                if config.get("mode") == "append"
                else translated
            )
            replacements.append((text_range, replacement))

        self.store.log("applying selection replacement")
        for text_range, replacement in reversed(replacements):
            text_range.setString(replacement)
        self.store.log("selection replacement applied")

    def _translate_document(self):
        document = self._document()
        config = self.store.load()
        translator = OpenAITranslator(config)
        paragraphs = []
        enumeration = document.Text.createEnumeration()
        while enumeration.hasMoreElements():
            element = enumeration.nextElement()
            if element.supportsService("com.sun.star.text.Paragraph"):
                original = element.getString()
                if original.strip():
                    paragraphs.append((element, original))

        if not paragraphs:
            raise TranslatorError("Le document ne contient aucun paragraphe à traduire.")

        replacements = []
        for paragraph, original in paragraphs:
            translated = self._translate_in_chunks(
                translator, original, int(config.get("max_chars", 9000))
            )
            replacement = (
                original + "\n" + translated
                if config.get("mode") == "append"
                else translated
            )
            replacements.append((paragraph, replacement))

        for paragraph, replacement in reversed(replacements):
            paragraph.setString(replacement)

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
        model = self.smgr.createInstanceWithContext(
            "com.sun.star.awt.UnoControlDialogModel", self.ctx
        )
        model.Width = 230
        model.Height = 174
        model.Title = "Traducteur IA LibreOffice"
        self._add_label(model, "apiLabel", 8, 8, 60, "Clé API OpenAI")
        self._add_edit(model, "apiKey", 72, 6, 150, config.get("api_key", ""), password=True)
        self._add_label(model, "urlLabel", 8, 30, 60, "URL de l'API")
        self._add_edit(model, "apiUrl", 72, 28, 150, config.get("api_url", ""))
        self._add_label(model, "modelLabel", 8, 52, 60, "Modèle")
        self._add_edit(model, "model", 72, 50, 150, config.get("model", ""))
        self._add_label(model, "sourceLabel", 8, 74, 60, "Langue source")
        self._add_edit(model, "source", 72, 72, 150, config.get("source_language", "Auto"))
        self._add_label(model, "targetLabel", 8, 96, 60, "Langue cible")
        self._add_edit(model, "target", 72, 94, 150, config.get("target_language", "French"))
        self._add_label(model, "modeLabel", 8, 118, 60, "Mode de sortie")
        self._add_list(
            model, "mode", 72, 116, 150,
            ("Remplacer le texte", "Ajouter la traduction"),
            1 if config.get("mode") == "append" else 0,
        )
        self._add_button(model, "save", 116, 146, 50, "Enregistrer")
        self._add_button(model, "cancel", 172, 146, 50, "Annuler")

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
            "com.sun.star.awt.MessageBoxType.ERRORBOX"
            if error else "com.sun.star.awt.MessageBoxType.INFOBOX"
        )
        buttons = uno.getConstantByName("com.sun.star.awt.MessageBoxButtons.BUTTONS_OK")
        frame = self.desktop.getCurrentFrame()
        parent = frame.getContainerWindow() if frame else None
        box = toolkit.createMessageBox(parent, box_type, buttons, title, message)
        try:
            box.execute()
        finally:
            try:
                box.dispose()
            except BaseException:
                pass


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    ExtensionHandler, IMPLEMENTATION_NAME, SERVICE_NAMES
)
