const year = document.querySelector('#year');
if (year) year.textContent = String(new Date().getFullYear());

const questions = [...document.querySelectorAll('.faq-list details')];
for (const question of questions) {
  question.addEventListener('toggle', () => {
    if (!question.open) return;
    for (const other of questions) {
      if (other !== question) other.open = false;
    }
  });
}
