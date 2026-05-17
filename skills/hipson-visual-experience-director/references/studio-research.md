# Dark Cinematic Portfolio Research

Source: distilled from `studio.pdf`.

These notes summarize repeatable design and implementation patterns from dark,
cinematic, interactive portfolios and creative studio sites. Use them as a
selection menu, not as a required style.

## Common Interaction Patterns

### Cinematic Hero

High-end portfolio sites often open with full-viewport video, interactive 3D,
or a strong media scene. The copy is usually minimal and the page invites the
user to scroll or explore.

Good fit:

- creative studios;
- premium portfolios;
- product launches;
- brand experiences where mood is part of the value.

Poor fit:

- dense SaaS workflows;
- compliance-heavy or text-heavy pages;
- products where users need immediate task completion.

### Scroll-Driven Storytelling

Case studies can be paced like short films: panels reveal, video segments
advance, 3D layers transition, and proof points arrive in a controlled order.

Use when:

- the story has clear acts;
- the user benefits from a guided reveal;
- assets are strong enough to carry the pacing.

Avoid when:

- the content is mostly reference material;
- users need to compare many items quickly;
- scroll hijacking would harm accessibility.

### Parallax And Depth

Layered movement, mouse tilt, and responsive 3D can add depth. Keep depth
subtle unless the whole experience is intentionally immersive.

Fallbacks:

- static hero image or short muted loop;
- reduced parallax distance;
- no mouse-driven camera movement on touch devices;
- no scroll scrub for reduced-motion users.

### Full-Screen Menus

Full-screen overlays can support cinematic navigation when they use large type,
clear focus states, and obvious escape behavior.

Requirements:

- keyboard reachable trigger;
- visible focus;
- Escape closes;
- background content is inert while open;
- animation has reduced-motion fallback.

### Custom Cursors

Custom cursors can signal drag, open, watch, or explore actions. They are
decorative unless they communicate a concrete state.

Use only on pointer devices. Preserve the native cursor or provide a simple
focus-visible alternative for keyboard and touch users.

### Audio

Ambient sound and sound effects can strengthen cinematic pages, but they must
be opt-in. Provide a clear mute state, never block the experience behind audio,
and ensure captions or text carry the meaning without sound.

### Layered Content Reveals

Panels, accordions, stacked cards, or mask reveals can manage information
density while keeping the page dramatic. The sequence should still be readable
with CSS and JavaScript disabled enough to expose the core content.

## Technical Ingredients

- WebGL or Three.js: 3D scenes, particles, shaders, liquid transitions, and
  spatial hero moments.
- GSAP or Anime.js: timeline-based animation, staggered reveals, scroll-driven
  orchestration, and precise easing.
- Barba.js or framework routing transitions: route-level continuity when the
  app needs persistent media or smooth page transitions.
- Locomotive Scroll or Lenis: smooth scroll and parallax orchestration when the
  interaction model justifies the extra layer.
- Lottie: lightweight vector animation and quick motion prototypes.
- Modern CSS grid/flex: reliable responsive structure under the visual effects.

Do not add these libraries by default. Choose the smallest tool that can deliver
the required behavior in the existing stack.

## Implementation Guidance

### Design Language

- Dark palette can feel premium, but only with disciplined contrast and spacing.
- Large type should clarify hierarchy, not become decoration.
- Media quality matters more than the number of effects.
- Use accents sparingly for active state, CTA, or narrative emphasis.

### Hero Experience

Potential implementations:

- short muted video loop;
- lightweight 3D object;
- shader-like transition over a static image;
- editorial hero with kinetic type only.

Acceptance checks:

- first meaningful content is visible quickly;
- CTA and navigation remain obvious;
- fallback still feels intentional;
- mobile version does not crop the key subject badly.

### Scrolling Narrative

Use pinned or scrubbed sections only for moments that benefit from exact timing.
Ordinary content should scroll normally. If a section depends on timing, define
what appears at each beat and how it exits.

### Transitions

Transitions should preserve orientation. Good transitions connect related
states: work card to project page, thumbnail to hero media, menu item to
section, or mode toggle to new visual language.

### Navigation

Navigation can be theatrical, but the information architecture must stay plain.
Users should always know where they are, what can be opened, and how to leave.

### Performance And Accessibility

- Compress and lazy-load media.
- Defer heavy 3D until after the initial content shell.
- Keep keyboard navigation and ARIA labels intact.
- Preserve contrast in dark mode.
- Disable or simplify animation under `prefers-reduced-motion`.
- Test desktop and mobile screenshots, not only code.

## Visual QA Checklist

- The design fits the business goal and audience.
- The first viewport communicates the offer or identity.
- Effects support hierarchy or story.
- Mobile has a purpose-built fallback.
- Reduced-motion users retain full content and navigation.
- Text contrast and focus states pass basic accessibility review.
- Heavy assets are lazy-loaded or optional.
- The final brief has concrete Codex acceptance criteria.
