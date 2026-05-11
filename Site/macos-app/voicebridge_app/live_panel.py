"""Panneau flottant macOS pour afficher transcript + traduction Live.

NSWindow non-modal avec NSTextView scrollable. Mis à jour sur le main
thread Cocoa via PyObjCTools.AppHelper.callAfter — sûr d'être appelé
depuis le thread asyncio de WSClient.

Format affiché :

    14:32:15  🎤 Bonjour comment ça va
    14:32:16  🇬🇧 Hello how are you
    ──────────────────────────────────
    14:32:42  🎤 J'ai pas mal de travail aujourd'hui
    14:32:43  🇬🇧 I have quite a bit of work today

Limité à MAX_LINES (≈ 200) lignes pour ne pas bouffer de RAM.
"""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger("voicebridge.panel")

# Limite de lignes — auto-pruning quand dépassé (FIFO).
MAX_LINES = 200

# Flags de fenêtre macOS (titre + fermer + redimensionner)
_STYLE_TITLED = 1 << 0
_STYLE_CLOSABLE = 1 << 1
_STYLE_MINIATURIZABLE = 1 << 2
_STYLE_RESIZABLE = 1 << 3


class LivePanel:
    """Encapsule la fenêtre PyObjC + le textview scrollable.

    Toutes les méthodes publiques doivent être appelées sur le main
    thread Cocoa (déjà le cas si appelées depuis rumps callbacks ou
    AppHelper.callAfter).
    """

    def __init__(self) -> None:
        self._window = None
        self._text_view = None
        self._scroll_view = None
        self._line_count = 0
        # Import lazy — si PyObjC absent (env de test), on no-op.
        try:
            from AppKit import (  # type: ignore # noqa: F401
                NSWindow, NSTextView, NSScrollView, NSBackingStoreBuffered,
                NSMakeRect, NSColor, NSFont, NSFloatingWindowLevel,
            )
            self._available = True
        except ImportError:
            log.warning("AppKit indisponible — LivePanel désactivé")
            self._available = False

    # ── Public ────────────────────────────────────────────────────────

    def show(self) -> None:
        if not self._available:
            return
        if self._window is None:
            self._create_window()
        self._window.makeKeyAndOrderFront_(None)

    def hide(self) -> None:
        if self._window is not None:
            self._window.orderOut_(None)

    def toggle(self) -> bool:
        """Bascule visible/masqué. Retourne True si visible après bascule."""
        if not self._available:
            return False
        if self._window is None or not self._window.isVisible():
            self.show()
            return True
        else:
            self.hide()
            return False

    def is_visible(self) -> bool:
        return (self._available and self._window is not None
                and self._window.isVisible())

    def append_transcript(self, text: str) -> None:
        """Ajoute une ligne 'micro' (texte transcrit, ce que l'user a dit)."""
        if not text or not text.strip():
            return
        self._append_line("🎤 " + text.strip(), color="text")

    def append_translated(self, text: str, target_lang: str = "en") -> None:
        """Ajoute une ligne 'traduction' (sortie traduite)."""
        if not text or not text.strip():
            return
        flag = {"en": "🇬🇧", "fr": "🇫🇷", "es": "🇪🇸", "de": "🇩🇪",
                "it": "🇮🇹", "pt": "🇵🇹", "nl": "🇳🇱", "ja": "🇯🇵",
                "zh": "🇨🇳"}.get(target_lang, "🌐")
        self._append_line(flag + " " + text.strip(), color="accent")

    def append_error(self, msg: str) -> None:
        """Ajoute une ligne d'erreur en rouge."""
        if not msg:
            return
        self._append_line("⚠️ " + msg.strip(), color="error")

    def append_separator(self) -> None:
        """Séparateur (fin de phrase) — ligne grise."""
        self._append_line("─" * 40, color="muted")

    def clear(self) -> None:
        if self._text_view is not None:
            self._text_view.setString_("")
            self._line_count = 0

    # ── Privé ─────────────────────────────────────────────────────────

    def _create_window(self) -> None:
        from AppKit import (
            NSWindow, NSTextView, NSScrollView, NSBackingStoreBuffered,
            NSMakeRect, NSColor, NSFont, NSFloatingWindowLevel,
        )

        style = (_STYLE_TITLED | _STYLE_CLOSABLE
                 | _STYLE_MINIATURIZABLE | _STYLE_RESIZABLE)
        # Position initiale : 480×360 px en haut à droite de l'écran
        rect = NSMakeRect(40, 40, 480, 360)
        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False,
        )
        self._window.setTitle_("VoiceBridge — Live transcript")
        # Reste au-dessus des autres fenêtres mais sans bloquer les clics
        self._window.setLevel_(NSFloatingWindowLevel)
        # Ne pas faire crash l'app quand on ferme la fenêtre (sinon
        # NSWindow se libère et notre référence devient invalide)
        self._window.setReleasedWhenClosed_(False)

        # ScrollView contenant la TextView
        scroll = NSScrollView.alloc().initWithFrame_(rect)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setAutohidesScrollers_(False)
        scroll.setBorderType_(0)  # NSNoBorder

        tv = NSTextView.alloc().initWithFrame_(rect)
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setRichText_(False)
        tv.setFont_(NSFont.fontWithName_size_("Menlo", 12.0)
                    or NSFont.userFixedPitchFontOfSize_(12.0))
        tv.setTextColor_(NSColor.labelColor())
        tv.setBackgroundColor_(NSColor.textBackgroundColor())
        tv.setMinSize_((480, 360))
        # Auto-resize avec le scroll view
        tv.setVerticallyResizable_(True)
        tv.setHorizontallyResizable_(False)
        tv.setAutoresizingMask_(1 << 1)  # width sizable

        scroll.setDocumentView_(tv)
        self._window.setContentView_(scroll)
        self._text_view = tv
        self._scroll_view = scroll

    def _append_line(self, text: str, color: str = "text") -> None:
        if not self._available:
            return
        if self._window is None:
            # Ne crée pas la fenêtre tant qu'on n'a pas explicitement
            # show() — on bufferise dans une string en attendant ? Non,
            # on drop silencieusement. L'historique vit dans /sessions.
            return
        if self._text_view is None:
            return

        ts = datetime.now().strftime("%H:%M:%S")
        line = f"{ts}  {text}\n"

        # Append au string courant
        current = self._text_view.string() or ""
        new = current + line

        # Auto-prune : si > MAX_LINES lignes, on garde les MAX_LINES dernières
        self._line_count += 1
        if self._line_count > MAX_LINES + 50:
            lines = new.splitlines(keepends=True)
            new = "".join(lines[-MAX_LINES:])
            self._line_count = MAX_LINES

        self._text_view.setString_(new)
        # Scroll vers le bas (toujours afficher le dernier)
        self._scroll_to_end()

    def _scroll_to_end(self) -> None:
        if self._text_view is None:
            return
        length = len(self._text_view.string())
        self._text_view.scrollRangeToVisible_((length, 0))
