(function () {
  function safeJsonParse(text) {
    try {
      return JSON.parse(text);
    } catch (e) {
      return null;
    }
  }

  function normalizeTablePayload(payload) {
    var table = (payload && payload.table) || {};
    var columns = Array.isArray(table.columns) ? table.columns : [];
    var rows = Array.isArray(table.rows) ? table.rows : [];
    var caption = typeof table.caption === 'string' ? table.caption : '';
    var notes_markdown = typeof table.notes_markdown === 'string' ? table.notes_markdown : '';

    // If empty, create a friendly default grid.
    if (columns.length === 0) {
      columns = [
        { key: 'size', label: 'Dydis' },
        { key: 'value_1', label: 'Reikšmė 1' },
        { key: 'value_2', label: 'Reikšmė 2' },
      ];
    }
    if (rows.length === 0) {
      rows = [{ size: '', value_1: '', value_2: '' }];
    }

    // Ensure unique keys
    var used = {};
    for (var i = 0; i < columns.length; i++) {
      var k = (columns[i] && columns[i].key) || '';
      k = (k + '').trim();
      if (!k) k = 'col_' + (i + 1);
      if (used[k]) {
        var n = 2;
        while (used[k + '_' + n]) n++;
        k = k + '_' + n;
      }
      used[k] = true;
      columns[i].key = k;
      if (typeof columns[i].label !== 'string') columns[i].label = k;
    }

    // Ensure all rows have all keys
    for (var r = 0; r < rows.length; r++) {
      var row = rows[r] || {};
      for (var c = 0; c < columns.length; c++) {
        var key = columns[c].key;
        if (row[key] === undefined || row[key] === null) row[key] = '';
      }
      rows[r] = row;
    }

    return {
      table: {
        caption: caption,
        columns: columns,
        rows: rows,
        notes_markdown: notes_markdown,
      },
    };
  }

  function initTableEditor(textarea) {
    if (!textarea || textarea.dataset.tableEditorInitialized === '1') return;
    textarea.dataset.tableEditorInitialized = '1';

    // Hide textarea; keep for submission.
    textarea.style.display = 'none';

    var wrapper = document.createElement('div');
    wrapper.className = 'table-editor-wrapper';
    wrapper.style.marginTop = '8px';

    var header = document.createElement('div');
    header.style.display = 'flex';
    header.style.gap = '8px';
    header.style.alignItems = 'center';
    header.style.marginBottom = '8px';

    var captionInput = document.createElement('input');
    captionInput.type = 'text';
    captionInput.placeholder = 'Lentelės pavadinimas (caption)';
    captionInput.style.flex = '1';

    var addRowBtn = document.createElement('button');
    addRowBtn.type = 'button';
    addRowBtn.className = 'button';
    addRowBtn.textContent = 'Pridėti eilutę';

    var addColBtn = document.createElement('button');
    addColBtn.type = 'button';
    addColBtn.className = 'button';
    addColBtn.textContent = 'Pridėti stulpelį';

    header.appendChild(captionInput);
    header.appendChild(addRowBtn);
    header.appendChild(addColBtn);

    var gridEl = document.createElement('div');
    gridEl.className = 'table-editor-grid';

    var footer = document.createElement('div');
    footer.style.marginTop = '8px';

    var notesLabel = document.createElement('div');
    notesLabel.textContent = 'Pastabos (markdown):';
    notesLabel.style.marginBottom = '4px';

    var notes = document.createElement('textarea');
    notes.rows = 3;
    notes.style.width = '100%';

    footer.appendChild(notesLabel);
    footer.appendChild(notes);

    wrapper.appendChild(header);
    wrapper.appendChild(gridEl);
    wrapper.appendChild(footer);

    textarea.parentNode.insertBefore(wrapper, textarea.nextSibling);

    if (!window.Tabulator) {
      // Fallback to raw textarea if CDN is blocked
      textarea.style.display = '';
      wrapper.parentNode.removeChild(wrapper);
      return;
    }

    var initial = safeJsonParse(textarea.value || '') || { table: { columns: [], rows: [] } };
    var payload = normalizeTablePayload(initial);

    captionInput.value = payload.table.caption || '';
    notes.value = payload.table.notes_markdown || '';

    function buildTabulatorColumns(cols) {
      return cols.map(function (c) {
        return {
          title: c.label || c.key,
          field: c.key,
          editor: 'input',
          headerSort: false,
        };
      });
    }

    var table = new window.Tabulator(gridEl, {
      data: payload.table.rows,
      columns: buildTabulatorColumns(payload.table.columns),
      layout: 'fitDataStretch',
      movableColumns: true,
      clipboard: true,
      clipboardPasteAction: 'replace',
      reactiveData: false,
      height: '360px',
    });

    function writeBack() {
      var cols = payload.table.columns;
      var rows = table.getData();

      // Ensure row keys
      for (var r = 0; r < rows.length; r++) {
        for (var c = 0; c < cols.length; c++) {
          var k = cols[c].key;
          if (rows[r][k] === undefined || rows[r][k] === null) rows[r][k] = '';
        }
      }

      var out = {
        table: {
          caption: captionInput.value || '',
          columns: cols,
          rows: rows,
          notes_markdown: notes.value || '',
        },
      };
      textarea.value = JSON.stringify(out);
    }

    // initial write
    writeBack();

    addRowBtn.addEventListener('click', function () {
      table.addRow({});
      writeBack();
    });

    addColBtn.addEventListener('click', function () {
      var idx = payload.table.columns.length + 1;
      var key = 'col_' + idx;
      payload.table.columns.push({ key: key, label: 'Stulpelis ' + idx });
      table.setColumns(buildTabulatorColumns(payload.table.columns));
      // Fill existing rows
      var rows = table.getData();
      for (var i = 0; i < rows.length; i++) {
        rows[i][key] = '';
      }
      table.replaceData(rows);
      writeBack();
    });

    captionInput.addEventListener('input', writeBack);
    notes.addEventListener('input', writeBack);

    table.on('dataChanged', writeBack);
    table.on('columnMoved', function (cols) {
      // Keep columns order in payload
      payload.table.columns = cols.map(function (c) {
        return {
          key: c.getField(),
          label: c.getDefinition().title || c.getField(),
        };
      });
      writeBack();
    });
  }

  function initAll(root) {
    var scope = root || document;
    var nodes = scope.querySelectorAll('textarea[data-table-editor="1"]');
    for (var i = 0; i < nodes.length; i++) {
      initTableEditor(nodes[i]);
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    initAll(document);

    // Django admin dynamic formsets (inlines)
    document.addEventListener('formset:added', function (e) {
      initAll(e.target);
    });
  });
})();
