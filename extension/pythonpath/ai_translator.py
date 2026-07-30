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
from com.sun.star.awt import XActionListener
from com.sun.star.beans import PropertyValue
from com.sun.star.frame import XDispatch, XDispatchProvider
from com.sun.star.lang import XServiceInfo

IMPLEMENTATION_NAME = "org.baimard.libreoffice.ai.translator.Handler"
SERVICE_NAMES = ("com.sun.star.frame.ProtocolHandler",)
PROTOCOL = "org.baimard.libreoffice.ai.translator:"
VERSION = "0.2.0"
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
        self._directory = None

    def directory(self):
        if self._directory is not None:
            return self._directory
        candidates = []
        try:
            provider = self.ctx.ServiceManager.createInstanceWithContext(
                "com.sun.star.util.PathSubstitution", self.ctx
            )
            user_url = provider.substituteVariables("$(user)", True)
            candidates.append(Path(uno.fileUrlToSystemPath(user_url)) / "ai-translator")
        except BaseException:
            pass
        candidates.extend(
            [
                Path.home() / ".config" / "libreoffice-ai-translator",
                Path("/tmp") / f"libreoffice-ai-translator-{os.getuid()}",
            ]
        )
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                probe = candidate / ".write-test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                self._directory = candidate
                return candidate
            except BaseException:
                continue
        self._directory = Path("/tmp")
        return self._directory

    def config_path(self):
        return self.directory() / "config.json"

    def log_path(self):
        return self.directory() / "extension.log"

    def load(self):
        config = dict(DEFAULT_CONFIG)
        try:
            data = json.loads(self.config_path().read_text(encoding="utf-8"))
            if isinstance(data, dict):
                config.update({key: value for key, value in data.items() if key in config})
        except (OSError, ValueError):
            pass
        return config

    def save(self, config):
        path = self.config_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)

    def log(self, message):
        try:
            with self.log_path().open("a", encoding="utf-8") as handle:
                stamp = datetime.now().isoformat(timespec="seconds")
                handle.write(f"[{stamp}] {message}\n")
                handle.flush()
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
        payload = {
            "model": self.config.get("model") or DEFAULT_CONFIG["model"],
            "instructions": (
                "Translate the supplied LibreOffice document text faithfully. "
                "Preserve paragraphs, line breaks, list markers, numbers, URLs, email addresses, "
                "placeholders and punctuation. Do not summarize, explain, annotate or add markdown. "
                f"Return only the translated text. Source language: {source}. Target language: {target}."
            ),
            "input": text,
        }
        request = urllib.request.Request(
            str(self.config.get("api_url") or DEFAULT_CONFIG["api_url"]),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "User-Agent": f"LibreOffice-AI-Translator/{VERSION}",
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
        direct = result.get("output_text")
        if isinstance(direct, str):
            return direct
        parts = []
        for item in result.get("output", []):
            if isinstance(item, dict):
                for content in item.get("content", []):
                    if isinstance(content, dict) and isinstance(content.get("text"), str):
                        parts.append(content["text"])
        if not parts:
            raise TranslatorError("L'API OpenAI n'a retourné aucun texte traduit.")
        return "".join(parts)


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
        self.store.log(f"handler loaded, version={VERSION}")

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
        self.store.log(f"dispatch start: {command}")
        try:
            if command == "configure":
                self._configure()
            elif command == "translate-selection":
                self._translate_selection()
            elif command == "translate-document":
                self._translate_document()
            else:
                raise TranslatorError(f"Commande inconnue : {command}")
            self.store.log(f"dispatch complete: {command}")
        except BaseException as exc:
            self.store.log(f"dispatch failed: {command}: {exc!r}\n{traceback.format_exc()}")
            try:
                self._message("Traducteur IA LibreOffice", str(exc), error=True)
            except BaseException:
                self.store.log("error dialog failed\n" + traceback.format_exc())

    def addStatusListener(self, listener, url):
        pass

    def removeStatusListener(self, listener, url):
        pass

    def _document(self):
        document = self.desktop.getCurrentComponent()
        if not document or not document.supportsService("com.sun.star.text.TextDocument"):
            raise TranslatorError("Ouvrez d'abord un document LibreOffice Writer.")
        return document

    def _translate_selection(self):
        document = self._document()
        controller = document.getCurrentController()
        selection = controller.getSelection()
        texts = []
        if hasattr(selection, "getCount"):
            for index in range(selection.getCount()):
                value = selection.getByIndex(index).getString()
                if value.strip():
                    texts.append(value)
        elif hasattr(selection, "getString"):
            value = selection.getString()
            if value.strip():
                texts.append(value)
        if not texts:
            raise TranslatorError("Sélectionnez le texte à traduire.")
        original = "\n".join(texts)
        config = self.store.load()
        self.store.log(f"selection captured: {len(original)} chars")
        translated = self._translate_in_chunks(
            OpenAITranslator(config), original, int(config.get("max_chars", 9000))
        )
        replacement = original + "\n" + translated if config.get("mode") == "append" else translated
        self.store.log(f"translation received: {len(replacement)} chars")

        # Let Writer replace the live selection through its own dispatch command.
        # This avoids retaining or modifying a stale XTextRange after the HTTP request.
        helper = self.smgr.createInstanceWithContext(
            "com.sun.star.frame.DispatchHelper", self.ctx
        )
        prop = PropertyValue()
        prop.Name = "Text"
        prop.Value = replacement
        frame = controller.getFrame()
        self.store.log("executing .uno:InsertText")
        helper.executeDispatch(frame, ".uno:InsertText", "", 0, (prop,))
        self.store.log(".uno:InsertText returned")

    def _translate_document(self):
        document = self._document()
        original = document.Text.getString()
        if not original.strip():
            raise TranslatorError("Le document ne contient aucun texte à traduire.")
        config = self.store.load()
        self.store.log(f"document captured: {len(original)} chars")
        translated = self._translate_in_chunks(
            OpenAITranslator(config), original, int(config.get("max_chars", 9000))
        )
        replacement = original + "\n" + translated if config.get("mode") == "append" else translated
        cursor = document.Text.createTextCursor()
        cursor.gotoEnd(True)
        self.store.log("replacing document through text cursor")
        cursor.setString(replacement)
        self.store.log("document replacement returned")

    @staticmethod
    def _translate_in_chunks(translator, text, max_chars):
        max_chars = max(1000, min(max_chars, 30000))
        if len(text) <= max_chars:
            return translator.translate(text)
        chunks, remaining = [], text
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
        model.Width, model.Height, model.Title = 230, 174, "Traducteur IA LibreOffice"
        self._add_label(model, "apiLabel", 8, 8, 60, "Clé API OpenAI")
        self._add_edit(model, "apiKey", 72, 6, 150, config.get("api_key", ""), True)
        self._add_label(model, "urlLabel", 8, 30, 60, "URL de l'API")
        self._add_edit(model, "apiUrl", 72, 28, 150, config.get("api_url", ""))
        self._add_label(model, "modelLabel", 8, 52, 60, "Modèle")
        self._add_edit(model, "model", 72, 50, 150, config.get("model", ""))
        self._add_label(model, "sourceLabel", 8, 74, 60, "Langue source")
        self._add_edit(model, "source", 72, 72, 150, config.get("source_language", "Auto"))
        self._add_label(model, "targetLabel", 8, 96, 60, "Langue cible")
        self._add_edit(model, "target", 72, 94, 150, config.get("target_language", "French"))
        self._add_label(model, "modeLabel", 8, 118, 60, "Mode de sortie")
        self._add_list(model, "mode", 72, 116, 150,
                       ("Remplacer le texte", "Ajouter la traduction"),
                       1 if config.get("mode") == "append" else 0)
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
                self.store.log("configuration saved")
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
        control.SelectedItems, control.Dropdown = (selected,), True
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
            "com.sun.star.awt.MessageBoxType.ERRORBOX" if error
            else "com.sun.star.awt.MessageBoxType.INFOBOX"
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
