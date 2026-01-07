(function () {
  function initTextarea(textarea) {
    if (!textarea || textarea.dataset.toastuiInitialized === '1') return;
    textarea.dataset.toastuiInitialized = '1';

    // Hide the original textarea; keep it for form submission.
    textarea.style.display = 'none';

    var container = document.createElement('div');
    container.className = 'toastui-editor-container';
    textarea.parentNode.insertBefore(container, textarea.nextSibling);

    var mode = textarea.dataset.toastuiMode || 'wysiwyg';
    var height = textarea.dataset.toastuiHeight || '520px';

    if (!window.toastui || !window.toastui.Editor) {
      // If CDN is blocked/unavailable, fall back to raw textarea.
      textarea.style.display = '';
      container.parentNode.removeChild(container);
      return;
    }

    var editor = new window.toastui.Editor({
      el: container,
      height: height,
      initialEditType: mode,
      previewStyle: 'vertical',
      usageStatistics: false,
      initialValue: textarea.value || '',
    });

    textarea._toastuiEditor = editor;
  }

  function initAll(root) {
    var scope = root || document;
    var nodes = scope.querySelectorAll('textarea[data-toastui-editor="1"]');
    for (var i = 0; i < nodes.length; i++) {
      initTextarea(nodes[i]);
    }
  }

  function syncForm(form) {
    if (!form) return;
    var nodes = form.querySelectorAll('textarea[data-toastui-editor="1"]');
    for (var i = 0; i < nodes.length; i++) {
      var textarea = nodes[i];
      if (
        textarea._toastuiEditor &&
        typeof textarea._toastuiEditor.getMarkdown === 'function'
      ) {
        textarea.value = textarea._toastuiEditor.getMarkdown();
      }
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    initAll(document);

    // Ensure Markdown is written back into the textarea before submit.
    document.addEventListener(
      'submit',
      function (e) {
        var target = e.target;
        if (
          target &&
          target.tagName &&
          target.tagName.toLowerCase() === 'form'
        ) {
          syncForm(target);
        }
      },
      true
    );
  });

  // Django admin dynamic formsets (inlines)
  document.addEventListener('formset:added', function (e) {
    initAll(e.target);
  });
})();
