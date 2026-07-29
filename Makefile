EXTENSION_NAME := libreoffice-ai-translator
EXTENSION_DIR := extension
DIST_DIR := dist
OXT := $(DIST_DIR)/$(EXTENSION_NAME).oxt

.PHONY: all build check install uninstall clean

all: build

build: check
	@mkdir -p $(DIST_DIR)
	@rm -f $(OXT)
	@cd $(EXTENSION_DIR) && zip -qr ../$(OXT) .
	@echo "Built $(OXT)"

check:
	@python3 -m py_compile $(EXTENSION_DIR)/pythonpath/ai_translator.py
	@python3 scripts/validate_extension.py $(EXTENSION_DIR)

install: build
	@command -v unopkg >/dev/null 2>&1 || { echo "ERREUR : unopkg introuvable."; exit 1; }
	@unopkg add --force $(OXT)
	@echo "Extension installed. Restart LibreOffice."

uninstall:
	@command -v unopkg >/dev/null 2>&1 || { echo "ERREUR : unopkg introuvable."; exit 1; }
	@unopkg remove org.baimard.libreoffice.ai.translator || true
	@echo "Extension removed. Restart LibreOffice."

clean:
	@rm -rf $(DIST_DIR) $(EXTENSION_DIR)/pythonpath/__pycache__ scripts/__pycache__
