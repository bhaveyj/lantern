const motion = window.matchMedia('(prefers-reduced-motion: no-preference)');
const walkthrough = window.matchMedia('(min-height: 500px) and (prefers-reduced-motion: no-preference)');
const hero = document.querySelector('.hero');
const story = document.querySelector('[data-story]');
const pin = story.querySelector('.story-pin');
const stage = story.querySelector('.story-stage');
const steps = [...story.querySelectorAll('.story-step')];
const reveals = [...document.querySelectorAll('[data-reveal]')];
let frame = 0;
let lastTime = 0;
let heroProgress;
let storyProgress;

function progressBetween(value, start, end) {
  return Math.max(0, Math.min(1, (value - start) / (end - start)));
}

function easeBetween(value, start, end) {
  const amount = progressBetween(value, start, end);
  return amount * amount * (3 - 2 * amount);
}

function drawStory(progress) {
  const select = easeBetween(progress, 0, 0.18);
  const pair = easeBetween(progress, 0.15, 0.34) * (1 - easeBetween(progress, 0.52, 0.74));
  const handoff = easeBetween(progress, 0.51, 0.77);
  const transfer = easeBetween(progress, 0.47, 0.56) * (1 - easeBetween(progress, 0.74, 0.83));
  const film = easeBetween(progress, 0.72, 0.81);
  const finish = easeBetween(progress, 0.79, 1);
  const faceForward = easeBetween(progress, 0.14, 0.67);

  const properties = {
    '--pair': pair,
    '--film': film,
    '--film-scale': 1 + 0.12 * finish,
    '--captions': easeBetween(progress, 0.81, 0.91),
    '--tv-angle': `${(-13 + 3 * select) * (1 - faceForward)}deg`,
    '--tv-scale': 0.9 + 0.1 * faceForward + 0.14 * finish,
    '--pc-x': `${-12 * film}%`,
    '--pc-y': `${-5 * select + 30 * faceForward + 18 * film}%`,
    '--pc-angle': `${9 + 12 * faceForward}deg`,
    '--pc-scale': 0.96 + 0.1 * select - 0.25 * faceForward - 0.23 * film,
    '--pc-opacity': 1 - 0.19 * faceForward - 0.81 * film,
    '--transfer': transfer,
    '--tile-x': `${-43 * (1 - handoff)}%`,
    '--tile-y': `${52 * (1 - handoff)}%`,
    '--tile-scale': 0.21 + 0.79 * handoff,
    '--glow': 0.5 + 0.23 * pair + 0.36 * film,
    '--glow-scale': 0.85 + 0.18 * pair + 0.24 * film,
  };

  for (const [property, value] of Object.entries(properties)) {
    stage.style.setProperty(property, value);
  }
  pin.style.setProperty('--story-progress', progress);

  const firstExit = easeBetween(progress, 0.17, 0.27);
  const pairEnter = easeBetween(progress, 0.24, 0.35);
  const pairExit = easeBetween(progress, 0.5, 0.61);
  const playEnter = easeBetween(progress, 0.58, 0.7);
  const copy = [
    [1 - firstExit, -18 * firstExit],
    [pairEnter * (1 - pairExit), 18 * (1 - pairEnter - pairExit)],
    [playEnter, 18 * (1 - playEnter)],
  ];
  steps.forEach((step, index) => {
    const [opacity, shift] = copy[index];
    step.style.setProperty('--step-opacity', opacity);
    step.style.setProperty('--step-shift', `${shift}px`);
  });
}

function follow(current, target, weight) {
  if (current === undefined || Math.abs(target - current) < 0.001) return target;
  return current + (target - current) * weight;
}

function render(time) {
  frame = 0;
  const elapsed = Math.min(time - lastTime, 34);
  const weight = 1 - Math.exp(-elapsed / 65);
  lastTime = time;

  const heroBounds = hero.getBoundingClientRect();
  const heroTarget = progressBetween(-heroBounds.top, 0, heroBounds.height * 0.85);
  const nextHero = follow(heroProgress, heroTarget, weight);

  let storyTarget = storyProgress;
  let nextStory = storyProgress;
  if (walkthrough.matches) {
    const storyBounds = story.getBoundingClientRect();
    storyTarget = progressBetween(-storyBounds.top, 0, storyBounds.height - pin.offsetHeight);
    nextStory = follow(storyProgress, storyTarget, weight);
  }

  if (nextHero !== heroProgress) {
    const exit = easeBetween(nextHero, 0, 1);
    hero.style.setProperty('--hero-shift', `${42 * exit}px`);
    hero.style.setProperty('--hero-scale', 1 + 0.08 * exit);
    hero.style.setProperty('--hero-opacity', 1 - 0.7 * exit);
    heroProgress = nextHero;
  }
  if (nextStory !== storyProgress) {
    drawStory(nextStory);
    storyProgress = nextStory;
  }
  if (heroProgress !== heroTarget || storyProgress !== storyTarget) queueRender();
}

function queueRender() {
  if (motion.matches && !frame) frame = window.requestAnimationFrame(render);
}

function updateLayout() {
  story.classList.toggle('is-animated', walkthrough.matches);
  if (frame) window.cancelAnimationFrame(frame);
  frame = 0;
  heroProgress = undefined;
  storyProgress = undefined;
  queueRender();
}

if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      if (motion.matches) entry.target.classList.add('is-revealing');
      observer.unobserve(entry.target);
    }
  }, { threshold: 0.14 });
  reveals.forEach((element) => observer.observe(element));
}

window.addEventListener('scroll', queueRender, { passive: true });
window.addEventListener('resize', queueRender);
motion.addEventListener('change', updateLayout);
walkthrough.addEventListener('change', updateLayout);
updateLayout();
