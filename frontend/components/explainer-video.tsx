'use client';

import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { BRAND_NAME } from '@/components/brand';
import { useTrack } from '@/components/story';

/**
 * Landing chapter: the explainer video. On lg screens with motion allowed the stage is sticky
 * and scroll grows the player from an inset card to the full stage. Elsewhere it renders at
 * full size in normal flow. It plays muted while on screen and pauses when scrolled away;
 * sound and full controls are one click away. The file is also offered as a download.
 */
const SRC = '/videos/ari-explainer.mp4';

export function ExplainerVideo() {
  const [ref, p, sticky] = useTrack<HTMLDivElement>();
  const video = useRef<HTMLVideoElement>(null);
  const [muted, setMuted] = useState(true);
  const [playing, setPlaying] = useState(false);
  const [userPaused, setUserPaused] = useState(false);

  // Grow over the first 45% of the track, then hold full size while the rest scrolls by.
  const grow = sticky ? Math.min(1, p / 0.45) : 1;
  // Smoothstep spreads growth across the chapter instead of front-loading the zoom.
  const ease = grow * grow * (3 - 2 * grow);
  const style = { '--vs': (0.62 + 0.38 * ease).toFixed(4), '--vr': `${(28 - 16 * ease).toFixed(1)}px` } as CSSProperties;

  useEffect(() => {
    const el = video.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver(([entry]) => {
      if (!entry) return;
      if (entry.intersectionRatio >= 0.35 && !userPaused) void el.play().catch(() => {});
      else if (entry.intersectionRatio < 0.35) el.pause();
    }, { threshold: [0, 0.35, 0.7] });
    io.observe(el);
    return () => io.disconnect();
  }, [userPaused]);

  const toggle = () => {
    const el = video.current;
    if (!el) return;
    if (el.paused) { setUserPaused(false); void el.play().catch(() => {}); } else { setUserPaused(true); el.pause(); }
  };
  const toggleSound = () => {
    const el = video.current;
    if (!el) return;
    el.muted = !el.muted;
    setMuted(el.muted);
    if (!el.muted && el.paused) { setUserPaused(false); void el.play().catch(() => {}); }
  };

  return (
    <div ref={ref} className="story-track story-track-2 bleed bleed-paper" id="explainer">
      <section aria-labelledby="explainer-title" className="story-stage flex flex-col justify-center gap-8 py-16">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.2fr_1fr] lg:items-end">
          <div>
            <p className="kicker">Watch</p>
            <h2 id="explainer-title" className="display mt-3 text-3xl font-semibold md:text-5xl">See {BRAND_NAME} in action.<br /><span className="text-muted">What it is, and how it works.</span></h2>
          </div>
          <p className="max-w-md text-sm text-muted">
            A walkthrough of the product: connecting a repository, the code review, the seven security agents and
            continuous defense.
          </p>
        </div>
        <figure className="explainer-frame" style={style} data-testid="explainer-video">
          <video
            ref={video}
            src={SRC}
            poster="/videos/ari-explainer-poster.jpg"
            className="explainer-video"
            muted
            loop
            playsInline
            preload="metadata"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onClick={toggle}
            aria-label={`${BRAND_NAME} explainer video`}
          />
          <figcaption className="explainer-controls">
            <button type="button" onClick={toggle} className="explainer-btn" aria-label={playing ? 'Pause video' : 'Play video'}>
              {playing ? 'Pause' : 'Play'}
            </button>
            <button type="button" onClick={toggleSound} className="explainer-btn" aria-pressed={!muted}>
              {muted ? 'Sound on' : 'Mute'}
            </button>
            <button type="button" onClick={() => void video.current?.requestFullscreen?.()} className="explainer-btn">Full screen</button>
            <a href={SRC} download="ari-explainer.mp4" className="explainer-btn">Download</a>
            <a href="#how" className="explainer-btn">Continue exploring ↓</a>
          </figcaption>
        </figure>
      </section>
    </div>
  );
}
