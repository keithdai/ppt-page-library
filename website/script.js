const year = document.querySelector('#year');
if (year) year.textContent = String(new Date().getFullYear());

const copyStatus = document.querySelector('#copy-status');
for (const button of document.querySelectorAll('[data-copy]')) {
  button.addEventListener('click', async () => {
    const code = document.getElementById(button.dataset.copy);
    if (!code) return;
    const label = button.textContent;
    try {
      await navigator.clipboard.writeText(code.textContent);
      button.textContent = '已复制';
      if (copyStatus) copyStatus.textContent = '已复制到剪贴板。';
    } catch {
      const range = document.createRange();
      range.selectNodeContents(code);
      const selection = window.getSelection();
      if (selection) {
        selection.removeAllRanges();
        selection.addRange(range);
      }
      const focusTarget = code.closest('pre') || code;
      if (!focusTarget.hasAttribute('tabindex')) focusTarget.setAttribute('tabindex', '-1');
      focusTarget.focus();
      button.textContent = '已选中';
      if (copyStatus) copyStatus.textContent = '已选中内容，请按 Command+C 或 Ctrl+C 复制。';
    }
    setTimeout(() => { button.textContent = label; }, 2000);
  });
}

const questions = [...document.querySelectorAll('.faq-list details')];
for (const question of questions) {
  question.addEventListener('toggle', () => {
    if (!question.open) return;
    for (const other of questions) {
      if (other !== question) other.open = false;
    }
  });
}
