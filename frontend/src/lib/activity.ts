// Tiny global "something is running" signal, so the header logo can animate
// while an analysis is in progress. The header lives in the server layout and
// can't see job state, so pages announce activity here instead.
//
// The animation never cuts off mid-spin: when activity ends, the logo finishes
// its current cycle and stops locked in the logo pose (see HeaderLogo).

let count = 0;
let playing = false;
let fallback: ReturnType<typeof setTimeout> | undefined;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function subscribeActivity(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
export const getActivityPlaying = () => playing;
export const getServerActivityPlaying = () => false;

/** Mark something as running. Returns a function that marks it finished. */
export function beginActivity(): () => void {
  count++;
  clearTimeout(fallback);
  if (!playing) {
    playing = true;
    emit();
  }
  let ended = false;
  return () => {
    if (ended) return;
    ended = true;
    count--;
    // Safety net for when no animation event fires (reduced motion, hidden tab).
    if (count === 0) fallback = setTimeout(settleActivity, 3600);
  };
}

/** Stop the animation, but only once nothing is running any more. */
export function settleActivity() {
  if (count > 0 || !playing) return;
  clearTimeout(fallback);
  playing = false;
  emit();
}
