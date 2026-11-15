const story = document.querySelector('[data-story]');
const stage = story.querySelector('.story-stage');
const steps = [...story.querySelectorAll('.story-step')];
const animatedLayout = window.matchMedia('(min-width: 680px) and (min-height: 440px) and (prefers-reduced-motion: no-preference)');
let frame = 0;
let inView = true;

function progressBetween(value, start, end) {
  return Math.max(0, Math.min(1, (value - start) / (end - start)));
}

function renderStory() {
  frame = 0;
  const readingLine = window.innerHeight * 0.5;
  const [first, second, third] = steps.map((step) => {
    const bounds = step.getBoundingClientRect();
    return bounds.top + bounds.height * 0.5;
  });
  const progress = readingLine < second
    ? progressBetween(readingLine, first, second) * 0.36
    : 0.36 + progressBetween(readingLine, second, third) * 0.64;

  const pair = progressBetween(progress, 0.13, 0.33) * (1 - progressBetween(progress, 0.51, 0.65));
  const handoff = progressBetween(progress, 0.49, 0.87);
  const transfer = progressBetween(progress, 0.49, 0.59) * (1 - progressBetween(progress, 0.76, 0.87));
  const film = progressBetween(progress, 0.72, 0.89);
  const captions = progressBetween(progress, 0.88, 0.98);
  const style = stage.style;

  style.setProperty('--pair', pair);
  style.setProperty('--film', film);
  style.setProperty('--captions', captions);
  style.setProperty('--tv-scale', 0.94 + 0.11 * film);
  style.setProperty('--pc-scale', 1 - 0.22 * pair - 0.3 * film);
  style.setProperty('--pc-opacity', 1 - 0.18 * pair - 0.78 * film);
  style.setProperty('--pc-offset', `${25 * pair + 10 * film}px`);
  style.setProperty('--transfer', transfer);
  style.setProperty('--tile-x', `${26 + 34 * handoff}%`);
  style.setProperty('--tile-y', `${66 - 21 * handoff}%`);
  style.setProperty('--tile-scale', 0.6 + 1.1 * handoff);
}

function queueRender() {
  if (animatedLayout.matches && inView && !frame) {
    frame = window.requestAnimationFrame(renderStory);
  }
}

function updateLayout() {
  story.classList.toggle('is-animated', animatedLayout.matches);
  if (frame) {
    window.cancelAnimationFrame(frame);
    frame = 0;
  }
  queueRender();
}

if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver(([entry]) => {
    inView = entry.isIntersecting;
    queueRender();
  });
  observer.observe(story);
}

window.addEventListener('scroll', queueRender, { passive: true });
window.addEventListener('resize', queueRender);
animatedLayout.addEventListener('change', updateLayout);
updateLayout();
