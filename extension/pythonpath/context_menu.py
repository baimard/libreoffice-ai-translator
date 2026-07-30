# -*- coding: utf-8 -*-
"""Dynamic Writer context-menu integration for LibreOffice AI Translator."""

from datetime import datetime
import os
import traceback

import unohelper
from com.sun.star.lang import XServiceInfo
from com.sun.star.task import XJob
from com.sun.star.ui import XContextMenuInterceptor
from com.sun.star.ui.ContextMenuInterceptorAction import EXECUTE_MODIFIED, IGNORED

IMPLEMENTATION_NAME = "org.baimard.libreoffice.ai.translator.ContextMenuJob"
# The job scheduler instantiates the value stored in Jobs.xcu as a UNO service.
# Therefore the implementation must explicitly advertise its own unique service name.
SERVICE_NAMES = (
    IMPLEMENTATION_NAME,
    "com.sun.star.task.Job",
)
COMMAND_URL = "org.baimard.libreoffice.ai.translator:translate-selection"
LOG_PATH = "/tmp/libreoffice-ai-translator-context-menu.log"


def _log(message):
    try:
        stamp = datetime.now().isoformat(timespec="seconds")
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] pid={os.getpid()} {message}\n")
    except BaseException:
        pass


_log("context_menu module imported")


class ContextMenuJob(
    unohelper.Base,
    XJob,
    XContextMenuInterceptor,
    XServiceInfo,
):
    def __init__(self, ctx):
        self.ctx = ctx
        self.smgr = ctx.ServiceManager
        self.desktop = self.smgr.createInstanceWithContext(
            "com.sun.star.frame.Desktop", ctx
        )
        self._registered_controller = None
        _log("ContextMenuJob instantiated")

    def getImplementationName(self):
        return IMPLEMENTATION_NAME

    def supportsService(self, service_name):
        return service_name in SERVICE_NAMES

    def getSupportedServiceNames(self):
        return SERVICE_NAMES

    def execute(self, arguments):
        _log(f"job execute called with {len(arguments) if arguments else 0} arguments")
        self._register_current_writer_controller()
        return None

    def _register_current_writer_controller(self):
        try:
            document = self.desktop.getCurrentComponent()
            if not document:
                _log("registration skipped: no current component")
                return
            if not document.supportsService("com.sun.star.text.TextDocument"):
                _log("registration skipped: current component is not Writer")
                return

            controller = document.getCurrentController()
            if controller is None:
                _log("registration skipped: Writer has no controller")
                return

            if self._registered_controller == controller:
                _log("registration skipped: controller already registered")
                return

            if not hasattr(controller, "registerContextMenuInterceptor"):
                _log("registration failed: controller has no context-menu interception interface")
                return

            controller.registerContextMenuInterceptor(self)
            self._registered_controller = controller
            _log("context menu interceptor registered on Writer controller")
        except BaseException:
            _log("context menu registration failed\n" + traceback.format_exc())

    def notifyContextMenuExecute(self, event):
        try:
            selection_supplier = getattr(event, "Selection", None)
            selection = (
                selection_supplier.getSelection()
                if selection_supplier is not None
                and hasattr(selection_supplier, "getSelection")
                else None
            )
            if not self._has_text_selection(selection):
                return IGNORED

            container = getattr(event, "ActionTriggerContainer", None)
            if container is None:
                _log("interception ignored: no ActionTriggerContainer")
                return IGNORED

            if self._contains_command(container, COMMAND_URL):
                return IGNORED

            # Keep the extension visually separated from Writer's built-in actions.
            if container.getCount() > 0:
                separator = self.smgr.createInstanceWithContext(
                    "com.sun.star.ui.ActionTriggerSeparator", self.ctx
                )
                separator.SeparatorType = 0
                container.insertByIndex(container.getCount(), separator)

            item = self.smgr.createInstanceWithContext(
                "com.sun.star.ui.ActionTrigger", self.ctx
            )
            item.Text = "Traduire la sélection"
            item.CommandURL = COMMAND_URL
            container.insertByIndex(container.getCount(), item)
            _log("context menu entry inserted")
            return EXECUTE_MODIFIED
        except BaseException:
            _log("context menu interception failed\n" + traceback.format_exc())
            return IGNORED

    @staticmethod
    def _has_text_selection(selection):
        if selection is None:
            return False

        if hasattr(selection, "getCount"):
            for index in range(selection.getCount()):
                item = selection.getByIndex(index)
                if hasattr(item, "getString") and item.getString().strip():
                    return True
            return False

        return bool(
            hasattr(selection, "getString") and selection.getString().strip()
        )

    @staticmethod
    def _contains_command(container, command_url):
        try:
            for index in range(container.getCount()):
                item = container.getByIndex(index)
                if getattr(item, "CommandURL", "") == command_url:
                    return True
        except BaseException:
            return False
        return False


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    ContextMenuJob,
    IMPLEMENTATION_NAME,
    SERVICE_NAMES,
)
