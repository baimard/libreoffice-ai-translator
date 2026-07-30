# -*- coding: utf-8 -*-
"""LibreOffice AI Translator extension."""

import json
import os
import ssl
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import uno
import unohelper
from com.sun.star.awt import XActionListener
from com.sun.star.frame import XDispatch, XDispatchProvider
from com.sun.star.lang import XServiceInfo
from com.sun.star.ui import XContextMenuInterceptor
from com.sun.star.ui.ContextMenuInterceptorAction import EXECUTE_MODIFIED, IGNORED

IMPLEMENTATION_NAME = "org.baimard.libreoffice.ai.translator.Handler"
SERVICE_NAMES = ("com.sun.star.frame.ProtocolHandler",)
PROTOCOL = "org.baimard.libreoffice.ai.translator:"
VERSION = "0.5.0"

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


class TranslationCancelled(TranslatorError):
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
        candidates.extend([
            Path.home() / ".config" / "libreoffice-ai-translator",
            Path("/tmp") / f"libreoffice-ai-translator-{os.getuid()}",
        ])
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
            path = self.log_path()
            if path.exists() and path.stat().st_size > 1024 * 1024:
                rotated = path.with_suffix(".log.1")
                try:
                    rotated.unlink()
                except OSError:
                    pass
                path.replace(rotated)
            with path.open("a", encoding="utf-8") as handle:
                stamp = datetime.now().isoformat(timespec="seconds")
                handle.write(f"[{stamp}] {message}\n")
        except BaseException:
            pass


class OpenAITranslator:
    def __init__(self, config):
        self.config = config

    def _request(self, instructions, input_value):
        api_key = str(self.config.get("api_key", "")).strip()
        if not api_key:
            raise TranslatorError("Aucune clé API OpenAI n'est configurée.")
        payload = {
            "model": self.config.get("model") or DEFAULT_CONFIG["model"],
            "instructions": instructions,
            "input": input_value,
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
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    parts.append(content["text"])
        if not parts:
            raise TranslatorError("L'API OpenAI n'a retourné aucun texte traduit.")
        return "".join(parts)

    def translate_segments(self, segments):
        source = self.config.get("source_language") or "Auto"
        target = self.config.get("target_language") or "French"
        data = {
            "segments": [
                {"id": str(segment_id), "text": text}
                for segment_id, text in segments
            ]
        }
        instructions = (
            "Translate each JSON segment faithfully. Preserve every segment id and return only "
            "valid JSON using exactly this schema: {\"segments\":[{\"id\":\"...\","
            "\"text\":\"...\"}]}. Do not merge, remove, reorder or create segments. "
            "Preserve numbers, URLs, email addresses, placeholders and punctuation. Do not add "
            "markdown or explanations. Source language: " + source + ". Target language: " + target + "."
        )
        raw = self._request(instructions, json.dumps(data, ensure_ascii=False))
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)
        try:
            decoded = json.loads(cleaned)
        except ValueError as exc:
            raise TranslatorError("L'API n'a pas retourné le JSON structuré attendu.") from exc
        returned = decoded.get("segments") if isinstance(decoded, dict) else None
        if not isinstance(returned, list):
            raise TranslatorError("La réponse structurée ne contient pas de segments.")
        mapping = {}
        for item in returned:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("text"), str):
                mapping[item["id"]] = item["text"]
        expected = {str(segment_id) for segment_id, _ in segments}
        if set(mapping) != expected:
            raise TranslatorError("La réponse structurée a perdu ou ajouté des segments.")
        return mapping


class DialogActionListener(unohelper.Base, XActionListener):
    def __init__(self, callback):
        self.callback = callback

    def actionPerformed(self, event):
        self.callback(event)

    def disposing(self, event):
        pass


class TranslationContextMenu(unohelper.Base, XContextMenuInterceptor):
    def __init__(self, owner, controller):
        self.owner = owner
        self.controller = controller

    def notifyContextMenuExecute(self, event):
        try:
            if not self.owner._selection_text(self.controller):
                return IGNORED
            container = event.ActionTriggerContainer
            if container is None:
                return IGNORED
            command_url = PROTOCOL + "translate-selection"
            for index in range(container.getCount()):
                if getattr(container.getByIndex(index), "CommandURL", "") == command_url:
                    return IGNORED
            trigger = container.createInstance("com.sun.star.ui.ActionTrigger")
            if trigger is None:
                raise RuntimeError("Impossible de créer com.sun.star.ui.ActionTrigger")
            trigger.setPropertyValue("Text", "Traduire la sélection")
            trigger.setPropertyValue("CommandURL", command_url)
            container.insertByIndex(container.getCount(), trigger)
            self.owner.store.log("context menu item added")
            return EXECUTE_MODIFIED
        except BaseException:
            self.owner.store.log("context menu failed\n" + traceback.format_exc())
            return IGNORED


class ExtensionHandler(unohelper.Base, XDispatchProvider, XDispatch, XServiceInfo):
    def __init__(self, ctx):
        self.ctx = ctx
        self.smgr = ctx.ServiceManager
        self.desktop = self.smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        self.store = ConfigStore(ctx)
        self._busy = False
        self._cancel_requested = False
        self._context_interceptors = {}
        self.store.log(f"handler loaded, version={VERSION}")
        self._ensure_context_menu()

    def getImplementationName(self):
        return IMPLEMENTATION_NAME

    def supportsService(self, service_name):
        return service_name in SERVICE_NAMES

    def getSupportedServiceNames(self):
        return SERVICE_NAMES

    def queryDispatch(self, url, target_frame_name, search_flags):
        self._ensure_context_menu()
        return self if url.Protocol == PROTOCOL else None

    def queryDispatches(self, descriptors):
        self._ensure_context_menu()
        return tuple(self.queryDispatch(d.FeatureURL, d.FrameName, d.SearchFlags) for d in descriptors)

    def _ensure_context_menu(self):
        try:
            document = self.desktop.getCurrentComponent()
            if not document or not document.supportsService("com.sun.star.text.TextDocument"):
                return
            controller = document.getCurrentController()
            key = str(hash(controller))
            if key in self._context_interceptors:
                return
            interceptor = TranslationContextMenu(self, controller)
            controller.registerContextMenuInterceptor(interceptor)
            self._context_interceptors[key] = interceptor
            self.store.log("context menu interceptor registered")
        except BaseException:
            self.store.log("context menu registration failed\n" + traceback.format_exc())

    def dispatch(self, url, arguments):
        self._ensure_context_menu()
        command = str(url.Path)
        self.store.log(f"dispatch start: {command}")
        if command == "cancel-translation":
            self._cancel_requested = bool(self._busy)
            return
        if command in ("translate-selection", "translate-document") and self._busy:
            self._message("Traducteur IA LibreOffice", "Une traduction est déjà en cours.")
            return
        indicator = None
        started = time.monotonic()
        try:
            if command in ("translate-selection", "translate-document"):
                self._busy = True
                self._cancel_requested = False
                indicator = self._status_indicator()
            if command == "configure":
                self._configure()
            elif command == "translate-selection":
                self._translate_selection(indicator)
            elif command == "translate-document":
                self._translate_document(indicator)
            else:
                raise TranslatorError(f"Commande inconnue : {command}")
            elapsed = time.monotonic() - started
            if indicator is not None:
                indicator.setText(f"Traduction terminée en {elapsed:.1f} s")
                indicator.setValue(100)
            self.store.log(f"dispatch complete: {command}, elapsed={elapsed:.1f}s")
        except TranslationCancelled as exc:
            self.store.log(f"dispatch cancelled: {command}: {exc}")
            if indicator is not None:
                indicator.setText("Traduction annulée")
        except BaseException as exc:
            self.store.log(f"dispatch failed: {command}: {exc!r}\n{traceback.format_exc()}")
            if indicator is not None:
                try:
                    indicator.setText("Échec de la traduction")
                except BaseException:
                    pass
            self._message("Traducteur IA LibreOffice", str(exc), error=True)
        finally:
            if command in ("translate-selection", "translate-document"):
                self._busy = False
                self._cancel_requested = False
                if indicator is not None:
                    try:
                        time.sleep(0.7)
                        indicator.end()
                    except BaseException:
                        pass

    def addStatusListener(self, listener, url):
        pass

    def removeStatusListener(self, listener, url):
        pass

    def _document(self):
        document = self.desktop.getCurrentComponent()
        if not document or not document.supportsService("com.sun.star.text.TextDocument"):
            raise TranslatorError("Ouvrez d'abord un document LibreOffice Writer.")
        return document

    def _status_indicator(self):
        try:
            frame = self.desktop.getCurrentFrame()
            indicator = frame.createStatusIndicator() if frame else None
            if indicator is not None:
                indicator.start("Analyse de la structure du document…", 100)
                indicator.setValue(0)
            return indicator
        except BaseException:
            return None

    @staticmethod
    def _selection_text(controller):
        selection = controller.getSelection()
        texts = []
        if hasattr(selection, "getCount"):
            for index in range(selection.getCount()):
                item = selection.getByIndex(index)
                if hasattr(item, "getString") and item.getString().strip():
                    texts.append(item.getString())
        elif hasattr(selection, "getString") and selection.getString().strip():
            texts.append(selection.getString())
        return "\n".join(texts)

    @staticmethod
    def _paragraph_units_from_enumeration(source):
        units = []
        if not hasattr(source, "createEnumeration"):
            return units
        enumeration = source.createEnumeration()
        while enumeration.hasMoreElements():
            element = enumeration.nextElement()
            try:
                if element.supportsService("com.sun.star.text.Paragraph") and hasattr(element, "getString"):
                    text = element.getString()
                    if text.strip():
                        units.append({"range": element, "original": text})
            except BaseException:
                continue
        return units

    def _selection_units(self, controller):
        selection = controller.getSelection()
        items = []
        if hasattr(selection, "getCount"):
            items = [selection.getByIndex(index) for index in range(selection.getCount())]
        else:
            items = [selection]
        units = []
        for item in items:
            text = item.getString() if hasattr(item, "getString") else ""
            if not text.strip():
                continue
            if "\n" in text:
                enumerated = self._paragraph_units_from_enumeration(item)
                if enumerated:
                    units.extend(enumerated)
                    continue
            units.append({"range": item, "original": text})
        return units

    def _document_units(self, document):
        units = self._paragraph_units_from_enumeration(document.Text)
        if not units and document.Text.getString().strip():
            units.append({"range": document.Text, "original": document.Text.getString()})
        return units

    @staticmethod
    def _batch_units(units, max_chars):
        max_chars = max(1000, min(int(max_chars), 30000))
        batches = []
        current = []
        current_size = 0
        for index, unit in enumerate(units):
            size = len(unit["original"]) + 80
            if current and current_size + size > max_chars:
                batches.append(current)
                current = []
                current_size = 0
            current.append((index, unit["original"]))
            current_size += size
        if current:
            batches.append(current)
        return batches

    def _translate_units(self, units, config, indicator):
        if not units:
            raise TranslatorError("Aucun paragraphe textuel à traduire n'a été trouvé.")
        translator = OpenAITranslator(config)
        batches = self._batch_units(units, config.get("max_chars", 9000))
        translated = {}
        total = len(batches)
        for position, batch in enumerate(batches, start=1):
            if self._cancel_requested:
                raise TranslationCancelled("Annulation demandée.")
            if indicator is not None:
                indicator.setText(f"Traduction du bloc structuré {position} sur {total}")
                indicator.setValue(int(((position - 1) / total) * 90))
            translated.update(translator.translate_segments(batch))
        if len(translated) != len(units):
            raise TranslatorError("Tous les paragraphes n'ont pas été traduits.")
        return translated

    @staticmethod
    def _verify_units_unchanged(units):
        for unit in units:
            if unit["range"].getString() != unit["original"]:
                return False
        return True

    def _apply_units(self, document, units, translated, mode, title):
        undo = self._undo_manager(document)
        entered = False
        try:
            if undo is not None:
                undo.enterUndoContext(title)
                entered = True
            for index, unit in enumerate(units):
                value = translated[str(index)]
                replacement = unit["original"] + "\n" + value if mode == "append" else value
                unit["range"].setString(replacement)
        finally:
            if entered:
                undo.leaveUndoContext()

    def _translate_selection(self, indicator):
        document = self._document()
        controller = document.getCurrentController()
        units = self._selection_units(controller)
        if not units:
            raise TranslatorError("Sélectionnez le texte à traduire.")
        config = self.store.load()
        self.store.log(f"structured selection captured: {len(units)} segments")
        translated = self._translate_units(units, config, indicator)
        if self._cancel_requested:
            raise TranslationCancelled("Annulation demandée avant insertion.")
        if not self._verify_units_unchanged(units):
            raise TranslatorError("La sélection a changé pendant la traduction. L'insertion a été annulée.")
        self._apply_units(document, units, translated, config.get("mode"), "Traduction IA structurée")
        self.store.log(f"structured selection applied: {len(units)} segments")

    def _translate_document(self, indicator):
        document = self._document()
        units = self._document_units(document)
        if not units:
            raise TranslatorError("Le document ne contient aucun texte à traduire.")
        config = self.store.load()
        self.store.log(f"structured document captured: {len(units)} segments")
        translated = self._translate_units(units, config, indicator)
        if self._cancel_requested:
            raise TranslationCancelled("Annulation demandée avant remplacement.")
        if not self._verify_units_unchanged(units):
            raise TranslatorError("Le document a été modifié pendant la traduction. Le remplacement a été annulé.")
        self._apply_units(document, units, translated, config.get("mode"), "Traduction IA du document")
        self.store.log(f"structured document applied: {len(units)} segments")

    @staticmethod
    def _undo_manager(document):
        try:
            return document.getUndoManager()
        except BaseException:
            return None

    def _configure(self):
        config = self.store.load()
        model = self.smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialogModel", self.ctx)
        model.Width, model.Height = 230, 184
        model.Title = f"Traducteur IA LibreOffice {VERSION}"
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
        self._add_list(model, "mode", 72, 116, 150, ("Remplacer le texte", "Ajouter la traduction"), 1 if config.get("mode") == "append" else 0)
        self._add_label(model, "versionLabel", 8, 142, 100, f"Version {VERSION}")
        self._add_button(model, "save", 116, 156, 50, "Enregistrer")
        self._add_button(model, "cancel", 172, 156, 50, "Annuler")
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
        control.Width, control.Height = width, 14
        control.StringItemList, control.SelectedItems, control.Dropdown = items, (selected,), True
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
