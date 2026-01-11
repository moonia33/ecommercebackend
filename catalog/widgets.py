from __future__ import annotations

from django import forms


class ToastUIMarkdownWidget(forms.Textarea):
    """Toast UI Editor widget that stores content as Markdown.

    Admin-side WYSIWYG editing with Markdown persistence.
    """

    class Media:
        css = {
            "all": (
                "https://uicdn.toast.com/editor/latest/toastui-editor.min.css",
            )
        }
        js = (
            "https://uicdn.toast.com/editor/latest/toastui-editor-all.min.js",
            "catalog/admin/toastui_markdown.js",
        )

    def __init__(
        self,
        attrs: dict | None = None,
        *,
        mode: str = "wysiwyg",
        height: str = "520px",
    ):
        final_attrs = dict(attrs or {})
        final_attrs.setdefault("data-toastui-editor", "1")
        final_attrs.setdefault("data-toastui-mode", mode)
        final_attrs.setdefault("data-toastui-height", height)
        # Make sure admin picks up a reasonable width even without custom CSS.
        final_attrs.setdefault("style", "width: 100%;")
        super().__init__(attrs=final_attrs)


class TableEditorWidget(forms.Textarea):
    class Media:
        css = {
            "all": (
                "https://unpkg.com/tabulator-tables@6.3.1/dist/css/tabulator.min.css",
            )
        }
        js = (
            "https://unpkg.com/tabulator-tables@6.3.1/dist/js/tabulator.min.js",
            "catalog/admin/table_editor.js",
        )

    def __init__(self, attrs: dict | None = None):
        final_attrs = dict(attrs or {})
        final_attrs.setdefault("data-table-editor", "1")
        final_attrs.setdefault("style", "width: 100%;")
        super().__init__(attrs=final_attrs)
