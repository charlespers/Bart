/* lib_blocks.js — runtime behavior for design-library blocks.
 * Vanilla JS, no framework. Idempotent: re-running on the same DOM
 * does no harm. Each block opt-in by data-attribute.
 */
(function () {
  'use strict';

  // ── Multiple choice ──────────────────────────────────────────
  function wireMultipleChoice(root) {
    var choices = root.querySelectorAll('.b-mc-choice');
    var explained = false;
    choices.forEach(function (btn, i) {
      btn.addEventListener('click', function () {
        if (root.dataset.locked) return;
        root.dataset.locked = '1';
        var pickedCorrect = btn.dataset.correct === 'true';
        choices.forEach(function (b) {
          b.disabled = true;
          if (b.dataset.correct === 'true') b.classList.add('b-mc-correct');
        });
        btn.classList.add(pickedCorrect ? 'b-mc-picked-correct' : 'b-mc-picked-incorrect');
        var explain = root.querySelector('[data-explain="' + i + '"]');
        if (explain) explain.hidden = false;
      });
    });
  }

  // ── Hotspots ─────────────────────────────────────────────────
  function wireHotspots(root) {
    var markers = root.querySelectorAll('.b-hotspot-marker');
    var popovers = root.querySelectorAll('.b-hotspot-popover');
    function close() {
      markers.forEach(function (m) { m.classList.remove('is-active'); });
      popovers.forEach(function (p) { p.classList.remove('is-open'); });
    }
    markers.forEach(function (m, i) {
      m.addEventListener('click', function () {
        var pop = root.querySelector('[data-popover="' + i + '"]');
        var open = pop && pop.classList.contains('is-open');
        close();
        if (!open && pop) {
          m.classList.add('is-active');
          pop.classList.add('is-open');
        }
      });
    });
    popovers.forEach(function (p) {
      p.addEventListener('click', close);
    });
  }

  // ── Checkpoint (spaced repetition) ───────────────────────────
  function wireCheckpoint(root) {
    var raw = root.dataset.cards || '[]';
    var cards;
    try { cards = JSON.parse(raw); } catch (e) { cards = []; }
    if (!cards.length) return;

    var idx = 0, flipped = false;
    var counts = { again: 0, hard: 0, good: 0, easy: 0 };

    var face = root.querySelector('[data-card-face]');
    var ratings = root.querySelector('[data-ratings]');
    var progressText = root.querySelector('.b-checkpoint-progress-text');
    var progressFill = root.querySelector('.b-checkpoint-progress-fill');

    function render() {
      if (idx >= cards.length) {
        renderDone();
        return;
      }
      face.textContent = flipped ? cards[idx].back : cards[idx].front;
      face.style.cursor = flipped ? 'default' : 'pointer';
      ratings.hidden = !flipped;
      progressText.textContent = idx + ' / ' + cards.length;
      progressFill.style.width = (100 * idx / cards.length) + '%';
    }

    function renderDone() {
      var total = cards.length;
      var score = Math.round(100 * (counts.good + counts.easy * 1.2) / total);
      face.style.cursor = 'default';
      face.innerHTML =
        '<div>' +
        '<div style="font-family:var(--font-body);font-weight:600;font-size:22px;letter-spacing:-0.01em;margin-bottom:8px">Section complete</div>' +
        '<div style="font-family:var(--font-serif);font-size:14px;color:var(--ink-mute);margin-bottom:16px">Confidence: <strong style="color:var(--ink)">' + score + '%</strong></div>' +
        '<div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap;font-family:var(--font-mono);font-size:10px;letter-spacing:0.04em;text-transform:uppercase">' +
          ['again','hard','good','easy'].map(function (k) {
            return '<span class="b-tag b-tag-' + ({again:'accent',hard:'warn',good:'info',easy:'ok'}[k]) + '">' + k + ' · ' + counts[k] + '</span>';
          }).join('') +
        '</div></div>';
      ratings.hidden = true;
      progressText.textContent = total + ' / ' + total;
      progressFill.style.width = '100%';
    }

    face.addEventListener('click', function () {
      if (idx >= cards.length) return;
      if (!flipped) { flipped = true; render(); }
    });

    ratings.querySelectorAll('[data-rating]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var r = btn.dataset.rating;
        if (counts[r] != null) counts[r]++;
        flipped = false;
        idx++;
        render();
      });
    });

    render();
  }

  // ── DragOrder ────────────────────────────────────────────────
  function wireDragOrder(root) {
    var list = root.querySelector('.b-drag-list');
    if (!list) return;
    var correct = [];
    try { correct = JSON.parse(root.dataset.correct || '[]'); } catch (e) {}
    var checkBtn = root.querySelector('[data-action="check"]');
    var resetBtn = root.querySelector('[data-action="reset"]');
    var status = root.querySelector('[data-status]');
    var initial = Array.from(list.children).map(function (li) { return li.outerHTML; }).join('');
    var dragSrc = null;

    list.addEventListener('dragstart', function (e) {
      var li = e.target.closest('.b-drag-item');
      if (!li) return;
      dragSrc = li;
      li.classList.add('is-dragging');
      try { e.dataTransfer.effectAllowed = 'move'; } catch (_) {}
    });
    list.addEventListener('dragend', function () {
      if (dragSrc) dragSrc.classList.remove('is-dragging');
      dragSrc = null;
    });
    list.addEventListener('dragover', function (e) {
      e.preventDefault();
      var li = e.target.closest('.b-drag-item');
      if (!li || !dragSrc || li === dragSrc) return;
      var rect = li.getBoundingClientRect();
      var before = (e.clientY - rect.top) < rect.height / 2;
      list.insertBefore(dragSrc, before ? li : li.nextSibling);
    });

    function renumber() {
      Array.from(list.children).forEach(function (li, i) {
        var idx = li.querySelector('.b-drag-idx');
        if (idx) idx.textContent = (i + 1) + '.';
      });
    }
    function check() {
      var allRight = true;
      Array.from(list.children).forEach(function (li, i) {
        var id = li.dataset.id;
        var want = correct.indexOf(id);
        var ok = want === i;
        li.classList.toggle('is-correct', ok);
        li.classList.toggle('is-wrong', !ok);
        var state = li.querySelector('[data-state]');
        if (state) state.textContent = ok ? '✓' : ('→ ' + (want + 1));
        li.draggable = false;
        if (!ok) allRight = false;
      });
      status.textContent = allRight ? 'all in order' : 'partial — keep going';
      status.className = 'b-drag-status ' + (allRight ? 'is-ok' : 'is-partial');
      checkBtn.hidden = true;
      resetBtn.hidden = false;
    }
    function reset() {
      list.innerHTML = initial;
      Array.from(list.children).forEach(function (li) { li.draggable = true; });
      renumber();
      status.textContent = '';
      status.className = 'b-drag-status';
      checkBtn.hidden = false;
      resetBtn.hidden = true;
    }
    checkBtn.addEventListener('click', check);
    resetBtn.addEventListener('click', reset);
    list.addEventListener('drop', renumber);
  }

  // ── FillInBlank ──────────────────────────────────────────────
  function wireFillInBlank(root) {
    var accept = [];
    try { accept = JSON.parse(root.dataset.accept || '[]'); } catch (e) {}
    var inputs = root.querySelectorAll('.b-fib-input');
    var btn = root.querySelector('[data-action="check"]');
    var status = root.querySelector('[data-status]');
    btn && btn.addEventListener('click', function () {
      var allOk = true;
      inputs.forEach(function (inp) {
        var idx = parseInt(inp.dataset.blank || '-1', 10);
        var want = accept[idx] || [];
        var got = String(inp.value || '').trim().toLowerCase();
        var ok = want.some(function (a) { return String(a).trim().toLowerCase() === got; });
        inp.classList.toggle('is-correct', ok);
        inp.classList.toggle('is-wrong', !ok);
        if (!ok) allOk = false;
      });
      status.textContent = allOk ? 'all correct' : 'review the marked blanks';
      status.className = 'b-fib-status ' + (allOk ? 'is-ok' : 'is-partial');
    });
  }

  // ── MatchPairs ───────────────────────────────────────────────
  function wireMatchPairs(root) {
    var lefts = root.querySelectorAll('.b-mp-left');
    var rights = root.querySelectorAll('.b-mp-right');
    var status = root.querySelector('[data-status]');
    var total = parseInt(root.dataset.total || '0', 10);
    var matched = 0;
    var selL = null, selR = null, lockTimer = null;

    function clearActive() {
      lefts.forEach(function (c) { if (!c.classList.contains('is-matched')) c.classList.remove('is-active', 'is-wrong'); });
      rights.forEach(function (c) { if (!c.classList.contains('is-matched')) c.classList.remove('is-active', 'is-wrong'); });
    }
    function tryMatch() {
      if (!selL || !selR) return;
      if (selL.dataset.id === selR.dataset.id) {
        selL.classList.add('is-matched'); selR.classList.add('is-matched');
        selL.classList.remove('is-active'); selR.classList.remove('is-active');
        matched++;
        selL = null; selR = null;
      } else {
        selL.classList.add('is-wrong'); selR.classList.add('is-wrong');
        var l = selL, r = selR;
        selL = null; selR = null;
        clearTimeout(lockTimer);
        lockTimer = setTimeout(function () { l.classList.remove('is-wrong', 'is-active'); r.classList.remove('is-wrong', 'is-active'); }, 600);
      }
      status.textContent = matched === total ? '✓ all matched' : (matched + ' / ' + total + ' matched');
      status.className = 'b-mp-status' + (matched === total ? ' is-ok' : '');
    }
    lefts.forEach(function (c) {
      c.addEventListener('click', function () {
        if (c.classList.contains('is-matched')) return;
        if (selL) selL.classList.remove('is-active');
        selL = c; c.classList.add('is-active'); tryMatch();
      });
    });
    rights.forEach(function (c) {
      c.addEventListener('click', function () {
        if (c.classList.contains('is-matched')) return;
        if (selR) selR.classList.remove('is-active');
        selR = c; c.classList.add('is-active'); tryMatch();
      });
    });
  }

  // ── ParameterSlider ──────────────────────────────────────────
  function wireParameterSlider(root) {
    var input = root.querySelector('.b-ps-input');
    var valueEl = root.querySelector('[data-value]');
    var renderEl = root.querySelector('[data-render]');
    var step = parseFloat(root.dataset.step || '1');
    var precision = step < 1 ? 2 : 0;
    var expr = root.dataset.expr || '';
    var fn = null;
    if (expr) {
      try { fn = new Function('v', 'return (' + expr + ');'); }
      catch (e) { fn = null; }
    }
    function update() {
      var v = parseFloat(input.value);
      valueEl.textContent = Number.isFinite(v) ? v.toFixed(precision) : input.value;
      if (fn) {
        try { renderEl.textContent = String(fn(v)); }
        catch (e) { renderEl.textContent = ''; }
      }
    }
    input.addEventListener('input', update);
    update();
  }

  // ── BuildEquation ────────────────────────────────────────────
  function wireBuildEquation(root) {
    var slots = root.querySelector('[data-slots]');
    var chips = root.querySelectorAll('.b-build-eq-chips .b-chip');
    var checkBtn = root.querySelector('[data-action="check"]');
    var clearBtn = root.querySelector('[data-action="clear"]');
    var status = root.querySelector('[data-status]');
    var correct = [];
    try { correct = JSON.parse(root.dataset.correct || '[]'); } catch (e) {}
    var placeholder = slots.querySelector('.b-build-eq-placeholder');
    var seq = [];

    function refresh() {
      // Remove all currently-placed chips except the placeholder
      Array.from(slots.querySelectorAll('.b-chip.is-placed')).forEach(function (c) { c.remove(); });
      if (seq.length === 0) {
        if (placeholder) placeholder.style.display = '';
      } else {
        if (placeholder) placeholder.style.display = 'none';
        seq.forEach(function (id, i) {
          var src = Array.from(chips).find(function (c) { return c.dataset.token === id; });
          if (!src) return;
          var clone = src.cloneNode(true);
          clone.classList.add('is-placed');
          clone.addEventListener('click', function () { seq.splice(i, 1); refresh(); status.textContent = ''; status.className='b-build-eq-status'; slots.classList.remove('is-correct','is-wrong'); });
          slots.appendChild(clone);
        });
      }
    }
    chips.forEach(function (c) {
      c.addEventListener('click', function () {
        seq.push(c.dataset.token);
        status.textContent = ''; status.className='b-build-eq-status';
        slots.classList.remove('is-correct','is-wrong');
        refresh();
      });
    });
    checkBtn.addEventListener('click', function () {
      var ok = seq.length === correct.length && seq.every(function (id, i) { return id === correct[i]; });
      slots.classList.toggle('is-correct', ok);
      slots.classList.toggle('is-wrong', !ok);
      status.textContent = ok ? '✓ correct' : 'not yet — try again';
      status.className = 'b-build-eq-status ' + (ok ? 'is-ok' : 'is-wrong');
    });
    clearBtn.addEventListener('click', function () {
      seq = []; refresh();
      status.textContent = ''; status.className='b-build-eq-status';
      slots.classList.remove('is-correct','is-wrong');
    });
  }

  // ── EstimateRange ────────────────────────────────────────────
  function wireEstimateRange(root) {
    var min = parseFloat(root.dataset.min);
    var max = parseFloat(root.dataset.max);
    var actual = parseFloat(root.dataset.actual);
    var unit = root.dataset.unit || '';
    var generous = parseFloat(root.dataset.generous || '0');
    var lo = root.querySelector('[data-lo]');
    var hi = root.querySelector('[data-hi]');
    var band = root.querySelector('[data-band]');
    var readout = root.querySelector('[data-readout]');
    var actualEl = root.querySelector('[data-actual]');
    var actualLabel = root.querySelector('[data-actual-label]');
    var revealBtn = root.querySelector('[data-action="reveal"]');
    var resetBtn = root.querySelector('[data-action="reset"]');
    var status = root.querySelector('[data-status]');
    function pct(v) { return ((v - min) / (max - min)) * 100; }
    function update() {
      var l = parseFloat(lo.value), h = parseFloat(hi.value);
      if (l > h) { l = h; lo.value = l; }
      band.style.left = pct(l) + '%';
      band.style.right = (100 - pct(h)) + '%';
      readout.textContent = l + '–' + h + unit;
    }
    lo.addEventListener('input', update);
    hi.addEventListener('input', update);
    revealBtn.addEventListener('click', function () {
      actualEl.style.left = pct(actual) + '%';
      actualEl.hidden = false;
      if (actualLabel) actualLabel.textContent = 'actual: ' + actual + unit;
      var l = parseFloat(lo.value), h = parseFloat(hi.value);
      var inRange = actual >= l && actual <= h;
      var tight = (h - l) <= generous;
      status.textContent = inRange
        ? (tight ? '★ in range, tight estimate' : '✓ in range')
        : '× outside your range';
      status.className = 'b-er-status ' + (inRange ? (tight ? 'is-tight' : 'is-ok') : 'is-out');
      revealBtn.hidden = true; resetBtn.hidden = false;
    });
    resetBtn.addEventListener('click', function () {
      actualEl.hidden = true;
      status.textContent = ''; status.className = 'b-er-status';
      revealBtn.hidden = false; resetBtn.hidden = true;
    });
    update();
  }

  // ── ConfidencePoll ───────────────────────────────────────────
  function wireConfidencePoll(root) {
    var opts = root.querySelectorAll('.b-cp-option');
    var commentary = root.querySelector('[data-commentary]');
    opts.forEach(function (btn) {
      btn.addEventListener('click', function () {
        opts.forEach(function (b) { b.classList.remove('is-active'); });
        btn.classList.add('is-active');
        commentary.textContent = btn.dataset.commentary || 'Logged.';
        commentary.hidden = false;
      });
    });
  }

  // ── Chem: BalanceEquation ────────────────────────────────────
  function parseFormula(f) {
    var out = {};
    var re = /([A-Z][a-z]?)(\d*)/g;
    var m;
    while ((m = re.exec(f))) {
      if (!m[1]) continue;
      out[m[1]] = (out[m[1]] || 0) + (m[2] ? +m[2] : 1);
    }
    return out;
  }
  function wireBalance(root) {
    var reactants = JSON.parse(root.dataset.reactants || '[]');
    var products = JSON.parse(root.dataset.products || '[]');
    var correct = JSON.parse(root.dataset.correct || '[]');
    var coefs = new Array(reactants.length + products.length).fill(1);
    var tally = root.querySelector('[data-tally] tbody');
    var status = root.querySelector('[data-status]');

    function update() {
      coefs.forEach(function (c, i) {
        var el = root.querySelector('[data-coef-idx="' + i + '"]');
        if (el) {
          el.textContent = c;
          el.classList.toggle('is-active', c !== 1);
        }
      });
      var left = {}, right = {};
      reactants.forEach(function (f, i) {
        var counts = parseFormula(f);
        Object.keys(counts).forEach(function (el) {
          left[el] = (left[el] || 0) + counts[el] * coefs[i];
        });
      });
      products.forEach(function (f, i) {
        var counts = parseFormula(f);
        Object.keys(counts).forEach(function (el) {
          right[el] = (right[el] || 0) + counts[el] * coefs[i + reactants.length];
        });
      });
      var elements = Object.keys(Object.assign({}, left, right)).sort();
      tally.innerHTML = elements.map(function (el) {
        var L = left[el] || 0, R = right[el] || 0;
        var ok = L === R;
        var d = R - L;
        var dStr = ok ? '✓' : (d > 0 ? '+' + d : String(d));
        var color = ok ? 'var(--ok)' : 'var(--warn)';
        return '<tr><td>' + el + '</td><td>' + L + '</td><td>' + R +
          '</td><td style="color:' + color + ';font-weight:700">' + dStr + '</td></tr>';
      }).join('');
      var balanced = elements.every(function (el) { return (left[el] || 0) === (right[el] || 0); });
      var minimal = balanced && correct.length === coefs.length &&
        coefs.every(function (c, i) { return c === correct[i]; });
      status.textContent = balanced
        ? (minimal ? '✓ balanced  ★ minimal coefficients' : '✓ balanced')
        : 'not yet balanced';
      status.className = 'b-balance-status' +
        (balanced ? ' is-balanced' : '') + (minimal ? ' is-minimal' : '');
    }
    root.querySelectorAll('.b-balance-step').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var idx = +btn.dataset.idx;
        var dir = btn.dataset.action === 'up' ? 1 : -1;
        coefs[idx] = Math.max(1, coefs[idx] + dir);
        update();
      });
    });
    update();
  }

  // ── Chem: IsomerSpotter ──────────────────────────────────────
  function wireIsomer(root) {
    var cards = root.querySelectorAll('.b-isomer-card');
    var checkBtn = root.querySelector('[data-action="check"]');
    var resetBtn = root.querySelector('[data-action="reset"]');
    var status = root.querySelector('[data-status]');
    var picks = new Set();
    cards.forEach(function (c) {
      c.addEventListener('click', function () {
        if (root.dataset.locked) return;
        var id = c.dataset.id;
        if (picks.has(id)) { picks.delete(id); c.classList.remove('is-picked'); }
        else { picks.add(id); c.classList.add('is-picked'); }
      });
    });
    checkBtn.addEventListener('click', function () {
      root.dataset.locked = '1';
      var allCorrect = true;
      cards.forEach(function (c) {
        var match = c.dataset.match === 'true';
        var picked = picks.has(c.dataset.id);
        c.classList.remove('is-picked');
        var ok = picked === match;
        c.classList.toggle('is-correct', ok);
        c.classList.toggle('is-wrong', !ok);
        var reason = c.querySelector('.b-isomer-reason');
        if (reason) reason.hidden = false;
        if (!ok) allCorrect = false;
      });
      status.textContent = allCorrect ? '✓ all sorted correctly' : 'review the marked ones';
      status.className = 'b-isomer-status ' + (allCorrect ? 'is-ok' : 'is-wrong');
    });
    resetBtn.addEventListener('click', function () {
      delete root.dataset.locked;
      picks.clear();
      cards.forEach(function (c) {
        c.classList.remove('is-picked', 'is-correct', 'is-wrong');
        var r = c.querySelector('.b-isomer-reason');
        if (r) r.hidden = true;
      });
      status.textContent = '';
      status.className = 'b-isomer-status';
    });
  }

  // ── Chem: TitrationCurve ─────────────────────────────────────
  function wireTitration(root) {
    var av = parseFloat(root.dataset.acidVol);
    var ac = parseFloat(root.dataset.acidConc);
    var bc = parseFloat(root.dataset.baseConc);
    var maxBase = parseFloat(root.dataset.maxBase);
    var pKa = root.dataset.pka != null ? parseFloat(root.dataset.pka) : null;
    var svg = root.querySelector('[data-svg]');
    var input = root.querySelector('[data-input]');
    var volEl = root.querySelector('[data-vol]');
    var phEl = root.querySelector('[data-ph]');

    function pHat(Vb) {
      var molesAcid = ac * av / 1000;
      var molesBase = bc * Vb / 1000;
      var totalV = (av + Vb) / 1000;
      if (pKa != null) {
        if (Vb === 0) return 0.5 * (pKa - Math.log10(ac));
        if (molesBase < molesAcid) {
          var HA = (molesAcid - molesBase) / totalV;
          var A = molesBase / totalV;
          return pKa + Math.log10(A / HA);
        }
        if (Math.abs(molesBase - molesAcid) < 1e-9) {
          var Csalt = molesAcid / totalV;
          var Kb = Math.pow(10, -(14 - pKa));
          var OH = Math.sqrt(Kb * Csalt);
          return 14 - (-Math.log10(OH));
        }
        var excess = (molesBase - molesAcid) / totalV;
        return 14 - (-Math.log10(excess));
      }
      if (molesBase < molesAcid) {
        var Hp = (molesAcid - molesBase) / totalV;
        return -Math.log10(Hp);
      }
      if (Math.abs(molesBase - molesAcid) < 1e-9) return 7;
      var OHs = (molesBase - molesAcid) / totalV;
      return 14 - (-Math.log10(OHs));
    }

    var W = 600, H = 280, pad = 44;
    var X = function (vol) { return pad + (vol / maxBase) * (W - pad * 2); };
    var Y = function (ph) { return H - pad - (ph / 14) * (H - pad * 2); };
    var equivalence = (ac * av) / bc;

    // Pre-compute curve once
    var curveSegs = [];
    for (var Vb = 0; Vb <= maxBase; Vb += 0.25) {
      var p = Math.max(0, Math.min(14, pHat(Vb)));
      curveSegs.push((curveSegs.length ? 'L' : 'M') + ' ' + X(Vb).toFixed(2) + ' ' + Y(p).toFixed(2));
    }
    var curvePath = curveSegs.join(' ');

    function paint() {
      var v = parseFloat(input.value);
      var cur = pHat(v);
      var phColor = cur < 7 ? '#c93030' : cur > 7 ? '#3050a4' : 'var(--ok)';
      var grid = '';
      [0, 2, 4, 6, 8, 10, 12, 14].forEach(function (p) {
        grid += '<line x1="' + pad + '" y1="' + Y(p) + '" x2="' + (W - pad) + '" y2="' + Y(p) +
          '" stroke="var(--rule)" stroke-dasharray="2 4" stroke-width="0.8"/>' +
          '<text x="' + (pad - 6) + '" y="' + (Y(p) + 4) + '" text-anchor="end" ' +
          'font-family="var(--font-mono)" font-size="11" fill="var(--ink-soft)">' + p + '</text>';
      });
      svg.innerHTML = grid +
        '<line x1="' + pad + '" y1="' + (pad / 2) + '" x2="' + pad + '" y2="' + (H - pad) +
        '" stroke="currentColor"/>' +
        '<line x1="' + pad + '" y1="' + (H - pad) + '" x2="' + (W - pad) + '" y2="' + (H - pad) +
        '" stroke="currentColor"/>' +
        '<line x1="' + X(equivalence) + '" y1="' + (pad / 2) + '" x2="' + X(equivalence) + '" y2="' + (H - pad) +
        '" stroke="var(--warn)" stroke-dasharray="4 3" stroke-width="1"/>' +
        '<text x="' + (X(equivalence) + 4) + '" y="' + (pad / 2 + 10) +
        '" font-family="var(--font-mono)" font-size="11" fill="var(--warn)">eq pt: ' +
        equivalence.toFixed(1) + ' mL</text>' +
        '<path d="' + curvePath + '" stroke="var(--accent)" stroke-width="2.4" fill="none"/>' +
        '<line x1="' + X(v) + '" y1="' + Y(cur) + '" x2="' + X(v) + '" y2="' + (H - pad) +
        '" stroke="' + phColor + '" stroke-width="1" stroke-dasharray="3 3"/>' +
        '<circle cx="' + X(v) + '" cy="' + Y(cur) + '" r="6" fill="' + phColor +
        '" stroke="var(--paper)" stroke-width="2"/>';
      volEl.textContent = v.toFixed(1) + ' mL';
      phEl.textContent = 'pH = ' + cur.toFixed(2);
      phEl.style.color = phColor;
    }
    input.addEventListener('input', paint);
    paint();
  }

  // ── Chem: ElectronConfig ─────────────────────────────────────
  function wireElectronConfig(root) {
    var Z = parseInt(root.dataset.atomicNumber, 10);
    var config = JSON.parse(root.dataset.config || '[]');
    var slots = root.querySelectorAll('.b-config-slot');
    var counter = root.querySelector('[data-counter]');
    var summary = root.querySelector('[data-summary]');
    var aufBtn = root.querySelector('[data-action="aufbau"]');
    var resetBtn = root.querySelector('[data-action="reset"]');

    function updateSlot(btn) {
      var n = parseInt(btn.dataset.electrons || '0', 10);
      btn.querySelector('.b-config-up').hidden = n < 1;
      btn.querySelector('.b-config-down').hidden = n < 2;
    }
    function totalPlaced() {
      var total = 0;
      slots.forEach(function (s) {
        total += parseInt(s.dataset.electrons || '0', 10);
      });
      return total;
    }
    function refresh() {
      slots.forEach(updateSlot);
      counter.textContent = 'placed: ' + totalPlaced() + '/' + Z;
      var bits = [];
      config.forEach(function (o, oi) {
        var n = 0;
        root.querySelectorAll('[data-orbital="' + oi + '"]').forEach(function (s) {
          n += parseInt(s.dataset.electrons || '0', 10);
        });
        if (n) {
          var stripped = o.label.replace(/[a-z]+$/, '');
          bits.push(stripped + '<sup>' + n + '</sup>');
        }
      });
      summary.innerHTML = bits.length ? 'config: ' + bits.join(' ') : '';
    }
    slots.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var n = parseInt(btn.dataset.electrons || '0', 10);
        btn.dataset.electrons = String((n + 1) % 3);
        refresh();
      });
    });
    aufBtn.addEventListener('click', function () {
      var remaining = Z;
      config.forEach(function (o, oi) {
        var rowSlots = root.querySelectorAll('[data-orbital="' + oi + '"]');
        rowSlots.forEach(function (s) { s.dataset.electrons = '0'; });
        // First pass: one electron each (Hund)
        for (var j = 0; j < rowSlots.length && remaining > 0; j++) {
          rowSlots[j].dataset.electrons = '1'; remaining--;
        }
        // Second pass: pair up
        for (var k = 0; k < rowSlots.length && remaining > 0; k++) {
          rowSlots[k].dataset.electrons = '2'; remaining--;
        }
      });
      refresh();
    });
    resetBtn.addEventListener('click', function () {
      slots.forEach(function (s) { s.dataset.electrons = '0'; });
      refresh();
    });
    refresh();
  }

  function wireAll() {
    document.querySelectorAll('[data-mc]').forEach(wireMultipleChoice);
    document.querySelectorAll('[data-hotspots]').forEach(wireHotspots);
    document.querySelectorAll('[data-checkpoint]').forEach(wireCheckpoint);
    document.querySelectorAll('[data-drag-order]').forEach(wireDragOrder);
    document.querySelectorAll('[data-fib]').forEach(wireFillInBlank);
    document.querySelectorAll('[data-mp]').forEach(wireMatchPairs);
    document.querySelectorAll('[data-parameter-slider]').forEach(wireParameterSlider);
    document.querySelectorAll('[data-build-eq]').forEach(wireBuildEquation);
    document.querySelectorAll('[data-estimate-range]').forEach(wireEstimateRange);
    document.querySelectorAll('[data-confidence-poll]').forEach(wireConfidencePoll);
    document.querySelectorAll('[data-balance]').forEach(wireBalance);
    document.querySelectorAll('[data-isomer]').forEach(wireIsomer);
    document.querySelectorAll('[data-titration]').forEach(wireTitration);
    document.querySelectorAll('[data-electron-config]').forEach(wireElectronConfig);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wireAll);
  } else {
    wireAll();
  }
})();
