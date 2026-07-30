# -*- coding: utf-8 -*-
"""Dynamic Writer context-menu integration for LibreOffice AI Translator."""

import traceback

import unohelper
from com.sun.star.lang import XServiceInfo
from com.sun.star.task import XJob
from com.sun.star.ui import XContextMenuInterceptor
from com.sun.star.ui.ContextMenuInterceptorAction import EXECUTE_MODIFIED, IGNORED

IMPLEMENTATION_NAME = "org.baimard.libreoffice.ai.translator.ContextMenuJob"
SERVICE_NAMES = ("com.sun.star.task.Job",)
COMMAND_URL = "org.baimard.libreoffice.ai.translator:translate-selection"


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
        self._registered_controllers = []

    def getImplementationName(self):
        return IMPLEMENTATION_NAME

    def supportsService(self, service_name):
        return service_name in SERVICE_NAMES

    def getSupportedServiceNames(self):
        return SERVICE_NAMES

    def execute(self, arguments):
        self._register_current_writer_controller()
        return None

    def _register_current_writer_controller(self):
        try:
            document = self.desktop.getCurrentComponent()
            if not document or not document.supportsService(
                "com.sun.star.text.TextDocument"
            ):
                return

            controller = document.getCurrentController()
            if controller is None:
                return

            for registered in self._registered_controllers:
                if registered == controller:
                    return

            controller.registerContextMenuInterceptor(self)
            self._registered_controllers.append(controller)
        except BaseException:
            self._log_error("context menu registration failed")

    def notifyContextMenuExecute(self, event):
        try:
            selection_supplier = event.Selection
            selection = selection_supplier.getSelection() if selection_supplier else None
            if not self._has_text_selection(selection):
                return IGNORED

            container = event.ActionTriggerContainer
            if container is None:
                return IGNORED

            if self._contains_command(container, COMMAND_URL):
                return IGNORED

            item = self.smgr.createInstanceWithContext(
                "com.sun.star.ui.ActionTrigger", self.ctx
            )
            item.Text = "Traduire la sélection"
            item.CommandURL = COMMAND_URL
            container.insertByIndex(container.getCount(), item)
            return EXECUTE_MODIFIED
        except BaseException:
            self._log_error("context menu interception failed")
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

    @staticmethod
    def _log_error(prefix):
        try:
            with open("/tmp/libreoffice-ai-translator-context-menu.log", "a", encoding="utf-8") as handle:
                handle.write(prefix + "\n" + traceback.format_exc() + "\n")
        except BaseException:
            pass


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    ContextMenuJob,
    IMPLEMENTATION_NAME,
    SERVICE_NAMES,
)
