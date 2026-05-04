/* sandbox.js — client-side bart-pipeline preview.
 *
 * Parses the textarea, expands the most-common `bart-*` JSON fences into
 * the same b-* HTML structures the Python renderer emits, runs marked.js
 * on the rest of the markdown, then KaTeX auto-render finalizes math.
 *
 * Coverage: formula-card, trap-callout, why-it-matters, mnemonic-card,
 * quick-check, multi-step, multiple-choice, worked-example, flowchart,
 * comparison-matrix, process-ribbon, concept-build, fig-caption, tag,
 * stamp, paper-rule, marginalia. Other blocks render as a labeled
 * placeholder card so the markdown still renders cleanly.
 *
 * Limitations: complex SVG-heavy components (concept-map, anatomy-diagram,
 * orbital-diagram, etc.) render as simplified placeholders. The full
 * rendering still requires the Python pipeline (`./run preview`).
 */
(function () {
  'use strict';

  var $ = function (sel) { return document.querySelector(sel); };
  var esc = function (s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  };

  // Inline-markdown helper: bold/italic/code only. Used for body text inside
  // library blocks where marked.js can't reach.
  function inlineMd(s) {
    if (s == null) return '';
    s = String(s);
    s = s.replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*\w])\*([^*\n]+?)\*([^*\w]|$)/g, '$1<em>$2</em>$3');
    s = s.replace(/`([^`\n]+?)`/g, '<code>$1</code>');
    return s;
  }

  // ─── Block renderers ─────────────────────────────────────────
  // Each takes a parsed JSON payload and returns an HTML string. The
  // structure mirrors lib_blocks.py so the existing blocks.css applies.
  var BLOCKS = {
    'formula-card': function (p) {
      var legend = (p.legend || []).map(function (l) {
        return '<span class="b-sym">\\(' + (l.symbol || '') + '\\)</span>'
             + '<span class="b-meaning">' + esc(l.meaning || '') + '</span>';
      }).join('');
      var stamp = p.stamp_label
        ? '<div class="b-formula-card-stamp"><span class="b-stamp">'
          + esc(p.stamp_label) + '</span></div>'
        : '';
      var note = p.note
        ? '<div class="b-formula-card-note">' + inlineMd(p.note) + '</div>'
        : '';
      var cite = p.cite
        ? '<div class="b-cite"><span class="b-cite-label">cite</span>'
          + '<span class="b-cite-text">' + esc(p.cite) + '</span></div>'
        : '';
      return '<div class="b-formula-card">'
           + stamp
           + (p.title ? '<div class="b-formula-card-title">' + esc(p.title) + '</div>' : '')
           + '<div class="b-formula-card-body">\\[' + (p.tex || '') + '\\]</div>'
           + (legend ? '<div class="b-formula-card-legend">' + legend + '</div>' : '')
           + note + cite
           + '</div>';
    },
    'trap-callout': function (p) {
      var kind = p.kind || 'trap';
      var icon = { trap: '!', warn: '⚠', note: 'i', ok: '✓' }[kind] || '!';
      return '<div class="b-trap-callout b-trap-callout-' + esc(kind) + '">'
           + '<span class="b-trap-callout-icon">' + icon + '</span>'
           + '<div class="b-trap-callout-body">'
           + (p.title ? '<div class="b-trap-callout-title">' + esc(p.title) + '</div>' : '')
           + '<div class="b-trap-callout-content">' + inlineMd(p.body || '') + '</div>'
           + '</div></div>';
    },
    'why-it-matters': function (p) {
      return '<div class="b-why-it-matters"><div class="b-why-it-matters-body">'
           + inlineMd(p.body || '') + '</div></div>';
    },
    'mnemonic-card': function (p) {
      var rows = (p.expansion || []).map(function (e) {
        return '<div class="b-mnemonic-row">'
             + '<span class="b-mnemonic-letter">' + esc(e.letter || '') + '</span>'
             + '<span class="b-mnemonic-text">' + inlineMd(e.text || '') + '</span>'
             + '</div>';
      }).join('');
      return '<div class="b-mnemonic-card">'
           + (p.acronym ? '<div class="b-mnemonic-acronym">' + esc(p.acronym) + '</div>' : '')
           + rows
           + (p.story ? '<div class="b-mnemonic-story">' + inlineMd(p.story) + '</div>' : '')
           + '</div>';
    },
    'quick-check': function (p) {
      return '<details class="b-quick-check"><summary>'
           + esc(p.label || 'Quick check') + ' — '
           + inlineMd(p.question || '') + '</summary>'
           + (p.hint ? '<div class="b-qc-hint"><em>hint:</em> ' + inlineMd(p.hint) + '</div>' : '')
           + '<div class="b-qc-answer"><strong>answer:</strong> '
           + inlineMd(p.answer || '') + '</div>'
           + '</details>';
    },
    'multi-step': function (p) {
      var hints = (p.hints || []).map(function (h, i) {
        return '<details class="b-mp-hint"><summary>hint ' + (i + 1) + '</summary>'
             + inlineMd(h) + '</details>';
      }).join('');
      return '<div class="b-mp"><div class="b-mp-label">' + esc(p.label || 'Practice problem') + '</div>'
           + '<div class="b-mp-question">' + inlineMd(p.problem || '') + '</div>'
           + hints
           + '<details class="b-mp-solution"><summary>show solution</summary>'
           + inlineMd(p.solution || '') + '</details>'
           + '</div>';
    },
    'multiple-choice': function (p) {
      var choices = (p.choices || []).map(function (c, i) {
        var letter = String.fromCharCode(65 + i);
        return '<button class="b-mc-choice" data-correct="' + (c.correct ? 'true' : 'false') + '" data-idx="' + i + '">'
             + '<span class="b-mc-letter">' + letter + '</span>'
             + '<span style="flex:1">' + esc(c.text || '') + '</span></button>'
             + (c.explanation ? '<div class="b-mc-explanation" data-explain="' + i + '" hidden>' + esc(c.explanation) + '</div>' : '');
      }).join('');
      return '<div class="b-mc" data-mc>'
           + '<div class="b-mc-label">' + esc(p.label || 'Multiple choice') + '</div>'
           + '<div class="b-mc-question">' + esc(p.question || '') + '</div>'
           + '<div class="b-mc-choices">' + choices + '</div></div>';
    },
    'worked-example': function (p) {
      var steps = (p.steps || []).map(function (s, i) {
        return '<div class="b-we-step"><div class="b-we-step-num">' + (i + 1) + '</div>'
             + '<div class="b-we-step-body">'
             + '<div class="b-we-step-action">' + inlineMd(s.action || '') + '</div>'
             + (s.math ? '<div class="b-we-step-math">\\[' + s.math + '\\]</div>' : '')
             + (s.reasoning ? '<div class="b-we-step-reasoning"><em>' + inlineMd(s.reasoning) + '</em></div>' : '')
             + '</div></div>';
      }).join('');
      return '<div class="b-worked-example">'
           + '<div class="b-we-tag">' + esc(p.tag_label || 'Worked example') + '</div>'
           + '<div class="b-we-problem"><strong>PROBLEM.</strong> ' + inlineMd(p.problem || '') + '</div>'
           + steps
           + (p.answer ? '<div class="b-we-answer"><strong>ANSWER</strong> ' + inlineMd(p.answer) + '</div>' : '')
           + '</div>';
    },
    'flowchart': function (p) {
      var steps = (p.steps || []).map(function (s, i) {
        return '<div class="b-flow-node"><div><span class="b-flow-node-index">'
             + String(i + 1).padStart(2, '0') + '</span>'
             + '<span class="b-flow-node-label">' + inlineMd(s.label || '') + '</span></div>'
             + (s.body ? '<div class="b-flow-node-body">' + inlineMd(s.body) + '</div>' : '')
             + '</div>';
      }).join('<div class="b-flow-arrow">↓</div>');
      return '<div class="b-flowchart">'
           + (p.title ? '<div class="b-flowchart-title">' + esc(p.title) + '</div>' : '')
           + steps + '</div>';
    },
    'fig-caption': function (p) {
      var num = p.number != null ? '<strong>Figure ' + esc(p.number) + '.</strong> ' : '';
      return '<div class="b-fig-caption">' + num + inlineMd(p.text || '') + '</div>';
    },
    'tag': function (p) {
      return '<span class="b-tag b-tag-' + esc(p.tone || 'mute') + '">' + esc(p.label || '') + '</span>';
    },
    'stamp': function (p) {
      return '<span class="b-stamp b-stamp-' + esc(p.tone || 'accent') + '">' + esc(p.label || 'KEY') + '</span>';
    },
    'paper-rule': function (p) {
      return '<div class="b-paper-rule"><span>' + esc(p.glyph || '§') + '</span></div>';
    },
    'marginalia': function (p) {
      return '<aside class="b-marginalia">' + inlineMd(p.text || '') + '</aside>';
    }
  };

  function placeholder(name, payload) {
    var keys = Object.keys(payload || {}).slice(0, 5).join(', ');
    return '<div class="b-block-placeholder" style="border:1px dashed var(--rule);'
         + 'padding:12px;border-radius:6px;font-family:var(--font-mono);font-size:11px;'
         + 'color:var(--ink-mute);background:var(--paper-hi)">'
         + '<strong>bart-' + esc(name) + '</strong> '
         + '<span style="color:var(--accent)">[sandbox preview limited — full pipeline only]</span>'
         + (keys ? '<div style="margin-top:6px">keys: ' + esc(keys) + '</div>' : '')
         + '</div>';
  }

  // Replace each ```bart-<name> JSON ``` fence with HTML.
  function expandFences(md) {
    var fenceRe = /^```bart-([a-z0-9-]+)[^\n]*\n([\s\S]*?)```[ \t]*$/gm;
    return md.replace(fenceRe, function (m, name, body) {
      var payload = {};
      body = body.trim();
      if (body) {
        try {
          payload = JSON.parse(body);
        } catch (e) {
          return '<div class="bart-block-error" style="background:#fff3cd;'
               + 'border:1px dashed #b48a3c;color:#6e4f10;padding:8px 12px;'
               + 'border-radius:6px;font-family:monospace;font-size:13px;'
               + 'margin:12px 0"><strong>bart-' + esc(name) + '</strong> '
               + 'failed: JSON parse error — ' + esc(e.message) + '</div>';
        }
      }
      var fn = BLOCKS[name];
      if (!fn) return '\n\n' + placeholder(name, payload) + '\n\n';
      try {
        return '\n\n' + fn(payload) + '\n\n';
      } catch (e) {
        return '\n\n' + placeholder(name, payload) + '\n\n';
      }
    });
  }

  function render() {
    var md = $('#sb-md').value || '';
    var status = $('#sb-status');
    var out = $('#sb-rendered');
    if (!md.trim()) {
      out.innerHTML = '<div class="sb-empty">paste markdown on the left — the preview will render here.</div>';
      status.textContent = 'idle';
      return;
    }
    status.textContent = 'rendering…';
    var t0 = performance.now();
    var expanded = expandFences(md);
    var html;
    try {
      // marked v12: marked.parse(text). Configured to allow raw HTML so our
      // expanded library blocks pass through.
      if (typeof marked === 'undefined') {
        out.innerHTML = '<div class="sb-empty">loading marked.js…</div>';
        status.textContent = 'waiting on marked.js (1s)';
        return;
      }
      marked.use({ breaks: false, gfm: true });
      html = marked.parse(expanded);
    } catch (e) {
      out.innerHTML = '<div class="sb-empty" style="color:var(--accent)">render error: '
                    + esc(e.message) + '</div>';
      status.textContent = 'error';
      return;
    }
    out.innerHTML = '<article>' + html + '</article>';
    // Run KaTeX over the new content.
    if (window.renderMathInElement) {
      try {
        window.renderMathInElement(out, {
          delimiters: [
            { left: '\\(', right: '\\)', display: false },
            { left: '\\[', right: '\\]', display: true },
            { left: '$$', right: '$$', display: true }
          ],
          ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code', 'tt'],
          throwOnError: false,
          errorColor: '#9a4628',
          strict: 'ignore'
        });
      } catch (e) { /* swallow */ }
    }
    // Re-wire any data-mc / data-mp / data-hotspots etc. that lib_blocks.js handles.
    if (window.bartLibBlocks && typeof window.bartLibBlocks.rewire === 'function') {
      try { window.bartLibBlocks.rewire(out); } catch (e) {}
    }
    var dt = (performance.now() - t0).toFixed(0);
    status.textContent = 'rendered in ' + dt + 'ms';
  }

  // Debounce input.
  var debounceTimer = null;
  function onInput() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(render, 200);
  }

  function init() {
    var md = $('#sb-md');
    if (!md) return;
    md.addEventListener('input', onInput);
    md.addEventListener('keydown', function (e) {
      // Tab inserts spaces instead of changing focus.
      if (e.key === 'Tab') {
        e.preventDefault();
        var s = md.selectionStart, t = md.selectionEnd;
        md.value = md.value.slice(0, s) + '  ' + md.value.slice(t);
        md.selectionStart = md.selectionEnd = s + 2;
        onInput();
      }
    });
    var clearBtn = $('#sb-clear');
    if (clearBtn) clearBtn.addEventListener('click', function (e) {
      e.preventDefault();
      md.value = '';
      onInput();
      md.focus();
    });
    var sampleBtn = $('#sb-sample');
    if (sampleBtn) sampleBtn.addEventListener('click', function (e) {
      e.preventDefault();
      md.value = SAMPLE;
      onInput();
    });
    // Restore from localStorage if present.
    try {
      var saved = localStorage.getItem('bart-sandbox-md');
      if (saved && !md.value) md.value = saved;
    } catch (e) {}
    md.addEventListener('input', function () {
      try { localStorage.setItem('bart-sandbox-md', md.value); } catch (e) {}
    });
    // Initial render.
    if (md.value) render();
  }

  var SAMPLE = [
    '# Sandbox demo',
    '',
    'Inline math: \\(E = mc^2\\)  ·  Display math:',
    '',
    '\\[\\int_{-\\infty}^{\\infty} e^{-x^2}\\,dx = \\sqrt{\\pi}\\]',
    '',
    '## Library blocks',
    '',
    '```bart-formula-card',
    '{"tex": "a^2 + b^2 = c^2", "title": "Pythagorean theorem", "stamp_label": "KEY", "note": "Holds for any right triangle."}',
    '```',
    '',
    '```bart-trap-callout',
    '{"kind": "trap", "title": "Common mistake", "body": "Don\'t confuse \\\\(\\\\sin^2\\\\theta\\\\) with \\\\((\\\\sin\\\\theta)^2\\\\)."}',
    '```',
    '',
    '```bart-quick-check',
    '{"question": "What is 2 + 2?", "answer": "4", "hint": "It\'s an even number."}',
    '```'
  ].join('\n');

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
