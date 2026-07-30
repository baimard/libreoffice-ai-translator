# -*- coding: utf-8 -*-
"""Local translation-memory layer for LibreOffice AI Translator."""

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

import unohelper

import ai_translator as core


VERSION = "0.6.0"
core.VERSION = VERSION


class TranslationMemory:
    """Small exact-match translation memory backed by a local SQLite file."""

    def __init__(self, path):
        self.path = Path(path)
        self._initialize()

    @staticmethod
    def _normalize_language(value):
        return " ".join(str(value or "").strip().casefold().split())

    @staticmethod
    def _normalize_text(value):
        return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    @classmethod
    def _key(cls, source_language, target_language, source_text):
        material = "\x1f".join(
            (
                cls._normalize_language(source_language),
                cls._normalize_language(target_language),
                cls._normalize_text(source_text),
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS translation_memory (
                    memory_key TEXT PRIMARY KEY,
                    source_language TEXT NOT NULL,
                    target_language TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    target_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    use_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_translation_memory_languages "
                "ON translation_memory(source_language, target_language)"
            )

    def lookup(self, source_language, target_language, source_text):
        source = self._normalize_text(source_text)
        if not source:
            return None

        key = self._key(source_language, target_language, source)
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT target_text FROM translation_memory WHERE memory_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE translation_memory "
                "SET use_count = use_count + 1, last_used_at = ? "
                "WHERE memory_key = ?",
                (now, key),
            )
        return row[0]

    def store(self, source_language, target_language, source_text, target_text):
        source = self._normalize_text(source_text)
        target = self._normalize_text(target_text)
        if not source or not target or source == target:
            return

        source_language = self._normalize_language(source_language)
        target_language = self._normalize_language(target_language)
        key = self._key(source_language, target_language, source)
        now = datetime.now().isoformat(timespec="seconds")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO translation_memory (
                    memory_key, source_language, target_language,
                    source_text, target_text, created_at,
                    updated_at, last_used_at, use_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(memory_key) DO UPDATE SET
                    target_text = excluded.target_text,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    source_language,
                    target_language,
                    source,
                    target,
                    now,
                    now,
                    now,
                ),
            )

    def stats(self):
        with self._connect() as connection:
            entries, hits = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(use_count), 0) "
                "FROM translation_memory"
            ).fetchone()
        size = self.path.stat().st_size if self.path.exists() else 0
        return int(entries), int(hits), int(size)


class ExtensionHandler(core.ExtensionHandler):
    """Core translator with transparent local-memory lookup and storage."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._memory = None
        self.store.log(f"local translation memory layer loaded, version={VERSION}")

    def _translation_memory(self):
        if self._memory is None:
            path = self.store.directory() / "translation-memory.sqlite3"
            try:
                self._memory = TranslationMemory(path)
                entries, hits, size = self._memory.stats()
                self.store.log(
                    "translation memory opened: "
                    f"entries={entries}, hits={hits}, bytes={size}"
                )
            except (OSError, sqlite3.Error) as exc:
                raise core.TranslatorError(
                    f"Impossible d'ouvrir la mémoire locale : {exc}"
                ) from exc
        return self._memory

    def _translate_units(self, units, config, indicator):
        if not units:
            raise core.TranslatorError(
                "Aucun paragraphe textuel à traduire n'a été trouvé."
            )

        source_language = config.get("source_language") or "Auto"
        target_language = config.get("target_language") or "French"
        memory = self._translation_memory()
        translated = {}
        missing = []
        hits = 0

        for index, unit in enumerate(units):
            cached = memory.lookup(
                source_language,
                target_language,
                unit["original"],
            )
            if cached is None:
                missing.append((index, unit["original"]))
            else:
                translated[str(index)] = cached
                hits += 1

        if indicator is not None and hits:
            indicator.setText(
                f"Mémoire locale : {hits} segment(s) réutilisé(s)"
            )
            indicator.setValue(5)

        if missing:
            translator = core.OpenAITranslator(config)
            batches = self._batch_segments(missing, config.get("max_chars", 9000))
            total = len(batches)
            for position, batch in enumerate(batches, start=1):
                if self._cancel_requested:
                    raise core.TranslationCancelled("Annulation demandée.")
                if indicator is not None:
                    indicator.setText(
                        f"Traduction du bloc structuré {position} sur {total} "
                        f"({hits} depuis la mémoire)"
                    )
                    indicator.setValue(5 + int(((position - 1) / total) * 85))

                batch_result = translator.translate_segments(batch)
                translated.update(batch_result)
                for segment_id, source_text in batch:
                    memory.store(
                        source_language,
                        target_language,
                        source_text,
                        batch_result[str(segment_id)],
                    )

        if len(translated) != len(units):
            raise core.TranslatorError(
                "Tous les paragraphes n'ont pas été traduits."
            )

        self.store.log(
            f"translation memory result: hits={hits}, misses={len(missing)}"
        )
        return translated

    @staticmethod
    def _batch_segments(segments, max_chars):
        max_chars = max(1000, min(int(max_chars), 30000))
        batches = []
        current = []
        current_size = 0

        for segment_id, text in segments:
            size = len(text) + 80
            if current and current_size + size > max_chars:
                batches.append(current)
                current = []
                current_size = 0
            current.append((segment_id, text))
            current_size += size

        if current:
            batches.append(current)
        return batches


# The manifest loads this component instead of the core component. The core
# module remains unchanged and reusable, which reduces regression risk.
g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    ExtensionHandler,
    core.IMPLEMENTATION_NAME,
    core.SERVICE_NAMES,
)
