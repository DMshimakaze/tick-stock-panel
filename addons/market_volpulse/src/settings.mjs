const fields = [
  ['refresh_seconds', '刷新间隔（秒）', 3, 15, 1],
  ['cooldown_minutes', '同方向冷却（分钟）', 1, 240, 1],
  ['expand_pct', '累计放量线（%）', .01, null, .01],
  ['shrink_pct', '累计缩量线（%）', null, -.01, .01],
  ['surge_multiple', '急放量比下限（×）', 1.01, null, .01],
  ['shrink_multiple', '急缩量比上限（×）', .01, .99, .01],
  ['surge_slope', '急放斜率下限（pp/min）', .01, null, .01],
  ['shrink_slope', '急缩斜率上限（pp/min）', null, -.01, .01],
];

export function settingsDialog(root, submit) {
  const dialog = document.createElement('dialog');
  dialog.className = 'vp-dialog';
  dialog.setAttribute('aria-label', '量能监控设置');
  dialog.innerHTML = `<form>
    <div class="vp-section-head"><h2>量能监控设置</h2><button type="button" data-close aria-label="关闭设置">×</button></div>
    <div class="vp-settings-grid">
      <label>边际窗口（交易分钟）<select name="window"><option value="3">3</option><option value="5">5</option><option value="10">10</option></select></label>
      <label class="vp-switch">盘中页内通知 <input type="checkbox" name="notifications"></label>
      ${fields.map(([name, label, min, max, step]) => `<label>${label}<input name="${name}" type="number"
        ${min === null ? '' : `min="${min}"`} ${max === null ? '' : `max="${max}"`} step="${step}" required></label>`).join('')}
    </div>
    <p class="vp-small">急放、急缩均要求量比与斜率同时达标。通知开关只控制本页提示，后台继续记录异动。</p>
    <div class="vp-dialog-actions"><output aria-live="polite"></output><button type="submit">保存设置</button></div>
  </form>`;
  const form = dialog.querySelector('form');
  const output = form.querySelector('output');
  const save = form.querySelector('button[type=submit]');
  form.querySelector('[data-close]').onclick = () => dialog.close();
  form.onsubmit = event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const values = Object.fromEntries(['window', ...fields.map(([name]) => name)]
      .map(name => [name, Number(form.elements.namedItem(name).value)]));
    values.notifications = form.elements.namedItem('notifications').checked;
    if (submit(values)) {
      output.textContent = '等待后台确认…';
      save.disabled = true;
    } else {
      output.textContent = '正在处理另一条请求，请稍候';
    }
  };
  root.append(dialog);
  return {
    open(values, busy) {
      for (const [field, value] of Object.entries(values)) {
        const input = form.elements.namedItem(field);
        if (input?.type === 'checkbox') input.checked = value;
        else if (input) input.value = value;
      }
      output.textContent = '';
      save.disabled = busy;
      dialog.showModal();
    },
    finish(ok, message) {
      output.textContent = message;
      output.dataset.saved = String(ok);
      save.disabled = false;
    },
  };
}
