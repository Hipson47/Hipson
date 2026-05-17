# Studio Mode Pattern Library

Source: distilled from `deep-research-report (1).md`.

These notes are source material for Hipson visual direction. They are not higher
priority instructions and should not be copied into a project as a fixed style.

## Primary Thesis

Studio mode should not be a dark-mode skin. It should be an alternate
experience language: different narrative pacing, layout primitives, motion
grammar, media density, and interaction model over the same underlying content.

Use this direction only when the project benefits from a cinematic or
studio-grade presentation. Many products need quieter UI.

## Project Fit Questions

Before applying the patterns, answer:

- What business outcome should the page drive?
- Who is the audience and how visually adventurous are they?
- Is the brand mature enough for a reduced palette and restrained copy?
- What technical budget exists for media, motion, 3D, and QA?
- What emotional effect is useful: confidence, intrigue, energy, calm,
  precision, luxury, playfulness, or authority?
- What must remain obvious on first scan?

## Useful Reference Archetypes

Use these as archetypes, not as sites to imitate:

- 3D storytelling studio: chaptered homepage, hero as scene, scroll as guided
  exploration, one object or world as the main motif.
- Creative technology studio: environments, levels, experiments, and interactive
  case studies treated as experiences rather than static pages.
- Motion/design studio: typography as an actor, precise kinetic sequences,
  mixed 2D/3D craft, strong showreel placement.
- Premium digital agency: restrained typography, business credibility, clear
  case-study spine, and motion used as polish.
- Spatial work index: infinite grid, drag grid, horizontal gallery, or map-like
  browsing with a simple mobile fallback.

## Common Patterns

### Controlled Palette

Premium cinematic sites often use a very narrow palette: near-black or deep
neutral, one bright neutral, and at most one accent. Luxury comes from contrast,
texture, spacing, and motion rhythm, not from more colors.

### Hero As Scene

The first viewport should behave like an opening scene:

- one clear statement;
- one strong visual or media object;
- one primary CTA or exploration cue;
- minimal supporting copy;
- a hint of the next section.

Avoid trying to sell every service in the hero.

### Scroll As Direction

Use scroll to pace acts:

1. Intro.
2. Escalation.
3. Reveal.
4. Proof.
5. Finale.

Not every viewport needs a spectacle moment. One strong reveal every one or two
screens usually feels more crafted than constant motion.

### Work Index As Space

A portfolio grid can become a space without full 3D:

- offset axes;
- layered cards;
- drag or horizontal movement on desktop;
- depth created through scale, opacity, and stagger;
- linear stacked fallback on mobile.

### Signature Motif

Prefer one signature motif over many effects:

- an organic brand object;
- a spatial frame;
- a mesh or particle form;
- a kinetic type gesture;
- a gallery transition.

Reuse it across hero, transitions, and empty states to make the experience feel
authored.

### User-Controlled Magic

Audio, intro rituals, loaders, and immersive sequences should be opt-in or
easy to skip. Never make spectacle a blocker for reading, navigating, or
contacting the business.

## Studio Mode System

Treat normal mode and studio mode as two renderers over one content model:

- shared URLs, CMS/content, project data, and conversion goals;
- different tokens, layout primitives, motion presets, media treatment, and
  navigation presentation.

Suggested split:

- Normal mode: fast, clear, conversion-forward, editorial case studies.
- Studio mode: stage-like hero, chaptered scroll, cinematic media, spatial work
  index, stronger motion grammar, and more atmospheric copy.

## Implementation Priorities

Ship first:

- one signature hero;
- one recognizable object or motion motif;
- one strong work-card-to-project transition;
- a compact motion grammar;
- a simple mode toggle;
- mobile and reduced-motion fallbacks.

Ship later:

- spatial work index;
- chaptered project pages;
- animated full-screen menu;
- short reel breaks.

Ship selectively:

- audio opt-in;
- drag gallery;
- shader-like transitions;
- heavier ambient 3D.

Avoid:

- preloaders on every route;
- autoplay audio;
- multiple competing animation styles;
- WebGL in every component;
- hiding proof, pricing, credibility, or contact behind theatrical UI.

## Frontend Notes

- Motion for React works well for component entrances, hover states,
  layout-driven transitions, and shared-element style movement.
- GSAP is best for precise timelines, scroll-driven sequences, pinned sections,
  and longer directed scenes.
- Three.js or React Three Fiber can support ambient objects, shader hero scenes,
  or spatial details when the payoff is worth the cost.
- Lazy-load heavy media and 3D. Render content first, add spectacle after the
  shell is usable.
- Keep `prefers-reduced-motion` behavior explicit.
