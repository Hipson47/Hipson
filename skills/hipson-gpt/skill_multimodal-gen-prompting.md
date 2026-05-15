---
name: multimodal-gen-prompting
description: >
  Prompt image and video generation models effectively. Covers Nano Banana/Pro/2
  (Gemini Image), Veo 3.1, Kling 3.0, Sora 2, Seedance 2.0 with model-specific
  guidance, multi-model routing, camera-first video prompting, Draft-to-Master
  workflows, and native audio direction. Refreshed to Q1 2026 state.
  Use when generating images or video with any AI model, designing a multi-model
  production pipeline, or writing prompts for visual content creation.
---

# Multimodal Generation Prompting

## 1. Purpose
Write effective prompts for image and video generation models. Route work to the right model. Build production-grade visual content pipelines.

## 2. When to Use
- Generating images with Nano Banana / Pro / 2
- Generating video with Veo 3.1, Kling 3.0, Sora 2, Seedance 2.0
- Designing multi-model routing workflows
- Building storyboards for multi-shot video production
- Optimizing prompt-to-output quality for visual content

## 3. When NOT to Use
- Text-only LLM tasks → `system-prompt-architect/`
- Code generation → `ai-coding-workflows/`
- Analyzing existing images/video (vision tasks) → use VLM analysis, not this skill

## 4. Core Concepts

### Multi-Model Routing [2026 Standard]
No single model dominates. Route by scene type:

| Scene Type | Model | Why |
|------------|-------|-----|
| Cinematic establishing shots | **Veo 3.1 Standard** | Best prompt adherence, scene consistency, 4K + native 48kHz audio |
| Human motion / character animation | **Kling 3.0** or **Seedance 2.0** | Motion control, multi-shot storyboard |
| Physics simulation / VFX | **Sora 2** | Fluid dynamics, gravity, particle effects |
| Lip-sync / directed performance | **Seedance 2.0** | Phoneme-level sync, reference video input |
| High-volume social / product | **Kling 3.0** | Best value ~$0.50/clip, native 4K |
| Budget / rapid iteration | **Veo 3.1 Lite** ($0.05/sec) or **Wan 2.6** (free, open-source) | Cost optimization |

### Image Models [April 2026]

| Model | Official Name | Best For |
|-------|---------------|----------|
| **Nano Banana** | Gemini 2.5 Flash Image | Speed, high-volume, conversational editing |
| **Nano Banana Pro** | Gemini 3 Pro Image | Complex compositions, text rendering, "Thinking" mode |
| **Nano Banana 2** | Gemini 3.1 Flash Image | Pro-level quality at Flash speed, default in Gemini app |

### Camera Direction Is King
"A mediocre scene description with great camera language will outperform a great scene description with no camera direction every time."

### Draft-to-Master Workflow
1. Generate **low-res previews** to test prompts (cheap, fast)
2. Pick best outputs
3. **Master** only winners to high-fidelity 4K
4. Prevents wasting credits on suboptimal clips

### Native Audio [2026 Table Stakes]
4 of 6 major video models now generate synchronized audio natively. Include audio direction in video prompts.

## 5. Video Prompt Formula

```
[Cinematography/Camera] + [Subject] + [Action/Motion] +
[Context/Environment] + [Style/Mood] + [Audio Direction]
```

**Minimum 50-100 words** per video prompt. Specify:
- Camera movement (dolly, tracking, crane, handheld, static)
- Temporal progression (what changes over the clip's duration)
- Color grading / lighting
- Aspect ratio (must match prompt language)
- Audio (ambient, dialogue, effects)

### Camera Vocabulary
Dolly in/out, tracking shot, crane shot, rack focus, shallow DOF, handheld/steadicam, POV, aerial/drone, slow-motion, time-lapse, whip pan, push-in, pull-back reveal

## 6. Image Prompt Patterns

### Nano Banana 2 / Pro Structure
```
[Subject + Adjectives] doing [Action] in [Location/Context].
[Composition/Camera Angle]. [Lighting/Atmosphere].
[Style/Media]. [Text/Constraint if any].
```

**Stop using 2023-era prompt spam.** No "4k, trending on artstation, masterpiece" — these models understand natural language.

### Text-First Hack (Nano Banana)
When generating images with text: first converse to generate text concepts, then request the image. The model renders text more accurately when it has "thought through" the text content first.

### Multi-Image Composition
Nano Banana 2 supports up to 14 reference images. Nano Banana Pro supports up to 8. Use for:
- Brand consistency (upload logo, color palette)
- Character consistency across scenes (upload character reference)
- Style transfer (upload style reference)

## 7. Model-Specific Video Prompting

### Kling 3.0
- **Multi-Shot Storyboard**: Define 3-12 shots with individual prompts, camera angles, transitions
- Native 4K (3840×2160, 60fps) — not upscaled
- Up to 2 minutes per clip
- Audio: scene-aware sound design
- Weakness: physics simulation, cinematic camera "feel"

### Veo 3.1
- Best prompt adherence and scene consistency
- "Ingredients to Video": upload reference images for character/object consistency
- Tiers: Lite ($0.05/sec, 720p), Fast, Standard ($0.40/sec, 4K + 48kHz audio)
- Weakness: human emotion nuance (Sora edges here)

### Sora 2
- Best physics simulation (fluid, gravity, particles)
- Best character consistency between cuts
- ~15-25 second clips
- Consumer access primarily via third-party APIs
- Weakness: limited availability, higher cost

### Seedance 2.0
- Upload **reference video** to define motion (dance, gestures, scenes)
- Phoneme-level lip-sync — best in class
- 2K output with cinematic camera work
- Weakness: lower max resolution than Kling/Veo

## 8. Failure Modes
1. **Vague prompt** → generic output. Fix: add camera direction, lighting, specific details.
2. **Wrong model for scene** → suboptimal quality. Fix: use routing table.
3. **Aspect ratio mismatch** → awkward framing. Fix: match prompt language to target format.
4. **Over-prompting** → confused output. Fix: remove "artstation, masterpiece" spam. Be descriptive, not repetitive.
5. **No audio direction** → silent or random audio. Fix: include ambient/dialogue/effects in prompt.
6. **Single-generation expectation** → disappointment. Fix: plan for 3-5 generations per final clip.

## 9. Cross-Links
- System prompt for generation pipelines → `system-prompt-architect/`
- Quality verification of generated content → `eval-security-guardrails/`
- DevOps pipeline architecture → preserved in legacy Multimodal DevOps Playbook (evaluation metrics: CLIP, FVD, identity consistency)

## 10. Source Basis
Delta §6 (all Q1 2026 model updates), Multimodal DevOps Playbook (pipeline architecture, evaluation metrics), Multimodalne modele generatywne (prompt skeletons — model-agnostic portions), Research.md Ch.2 (video physics compliance, camera control).

## 11. Freshness Notes
`[FRESHNESS: April 2026]` Kling 3.0, Seedance 2.0, Nano Banana 2 are current. Monitor for: Kling 3.x updates, Veo 3.2, Sora Pro tier changes, new Seedance versions. Pricing is approximate and changes frequently.
---

# Multimodal Generation — Examples

## Example 1: Bad vs Better — Video Prompt

**Bad:**
```
A dog running in a field
```

**Better:**
```
Cinematic tracking shot following a golden retriever sprinting
through a sun-drenched wildflower meadow. Camera at knee height,
slightly behind the dog, smooth steadicam movement. Golden hour
lighting with lens flares. Shallow depth of field — dog sharp,
background bokeh. The dog's ears flap with each stride, paws
kicking up small clouds of pollen. Ambient sound: wind through
grass, distant birdsong, the rhythmic thud of paws on soft earth.
Warm color grade, 24fps cinematic. 16:9 aspect ratio.
```

**Why:** Camera direction (tracking, knee height, steadicam), temporal detail (ears flap, paws kick), lighting, audio direction, aspect ratio. Every element guides the model.

---

## Example 2: Bad vs Better — Image Prompt (Nano Banana)

**Bad:**
```
4k, trending on artstation, masterpiece, ultra detailed,
best quality, a coffee shop interior
```

**Better:**
```
A cozy Scandinavian-style coffee shop interior on a rainy
afternoon. Warm Edison bulb lighting reflecting off rain-streaked
floor-to-ceiling windows. A barista in a cream apron
preparing a pour-over at a blonde wood counter. Steam rising
from the kettle catches the light. Potted monstera in the corner.
Shot from a seated customer's perspective, shallow depth of field.
```

---

## Example 3: Multi-Shot Storyboard (Kling 3.0)

```
Generate a 4-shot product launch sequence:

Shot 1: Close-up of hands unboxing a matte black device from
minimal packaging. Slow, deliberate movements. Soft ambient light.
ASMR-style sound of cardboard and foam.

Shot 2: Medium shot — the device placed on a clean white desk.
Camera slowly dollies in. A finger touches the power button.
The screen illuminates with a soft blue glow. Subtle electronic chime.

Shot 3: Over-the-shoulder shot of a person using the device.
Screen shows a clean UI. Natural office ambient sound.
Rack focus from person to screen.

Shot 4: Wide establishing shot — modern open office. Multiple people
using the device at different workstations. Morning light through
large windows. Upbeat ambient music fades in. Camera slowly cranes up.

Maintain visual consistency: same device design, same color palette,
same lighting temperature across all shots.
```

---

## Example 4: Nano Banana Pro — Text Rendering

```
A glossy magazine cover with large bold serif text reading
"DESIGN MATTERS" filling the upper third. Below the text,
a portrait of a woman in minimal attire against a gradient
background shifting from coral to deep navy. The magazine title
"STUDIO" in small caps at the top. Issue "Vol. 12 | April 2026"
and a barcode in the bottom right corner.
```

---

## Example 5: Seedance 2.0 — Directed Motion

```
[Upload reference video of a person doing sign language]

Using the motion from the reference video, generate a scene of
a professional interpreter in a navy blazer performing the same
signs. Studio setting with soft key light from the left.
Clean gray background. Camera: static medium shot, chest up.
Maintain exact hand positions and timing from reference.
Lip movements should match the spoken translation overlaid
as subtitle text.
```

---

## Example 6: Draft-to-Master Workflow

```
DRAFT PHASE (low-res, fast, cheap):
  Generate 5 variations of: "Aerial drone shot of a coastal
  Italian village at sunset, warm Mediterranean light..."
  Resolution: 720p, fastest available tier.
  
REVIEW:
  Select best 2 based on: composition, color, building geometry,
  water reflection quality.

MASTER PHASE (high-res, quality):
  Regenerate selected 2 at:
  - 4K resolution (Veo 3.1 Standard or Kling 3.0)
  - Native audio enabled
  - 8 seconds duration
```

---

## Example 7: Multi-Model Routing in Practice

```
Project: 30-second fashion brand ad

Shot 1 (establishing): Veo 3.1 Standard
  "Sweeping crane shot of a Parisian rooftop at golden hour..."

Shot 2 (product): Kling 3.0
  "Close-up tracking shot of model's hand brushing fabric..."
  
Shot 3 (performance): Seedance 2.0
  [Upload reference: model walking runway]
  "Transfer runway walk motion to urban street setting..."

Shot 4 (VFX): Sora 2
  "Fabric transforms into flowing liquid gold, defying gravity..."

Assembly: CapCut for cuts, transitions, text overlays, final grade.
```

---

# Multimodal Generation — Checklist

## Pre-Flight
- [ ] Model selected based on scene type (see routing table)
- [ ] Aspect ratio decided and matches prompt language
- [ ] Budget tier selected (Draft vs Master)
- [ ] Audio requirements identified (ambient, dialogue, effects, silent)
- [ ] Reference images/videos prepared if needed (character consistency)

## In-Flight
- [ ] Prompt includes camera direction as the FIRST element
- [ ] Prompt is 50-100+ words for video
- [ ] Temporal progression described (what changes over time)
- [ ] Lighting and color grading specified
- [ ] Audio direction included if model supports it
- [ ] No 2023-era keyword spam ("trending on artstation")
- [ ] Generating 3-5 variations per final clip

## Final Review
- [ ] Visual consistency across multi-shot sequences
- [ ] Character identity maintained between cuts
- [ ] Physics look plausible (no floating objects, weird gravity)
- [ ] Text is legible and correctly spelled (for image gen)
- [ ] Audio matches visual content (sync check)
- [ ] Aspect ratio renders correctly for target platform

## Top 5 Failure Modes
1. **No camera direction** → flat, generic output
2. **Wrong model for scene** → suboptimal quality (e.g., Kling for physics-heavy VFX)
3. **Single generation expectation** → disappointment; always plan 3-5 variations
4. **Prompt spam** → "masterpiece, 4k, trending" dilutes real instructions
5. **Missing audio direction** → silent output or random irrelevant sounds
