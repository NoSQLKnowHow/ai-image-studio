// JSON shapes returned by the backend (backend/studio/serialize.py, api.py, jobs.py).

/** Pictures are made in one of two modes; music (DESIGN.md §26) and vector (§31) are two more kinds of run, each on its own tab. */
export type ImageMode = "generate" | "edit";
export type Mode = ImageMode | "music" | "vector";
/** The two models the studio can hold, one at a time (DESIGN.md §26.4). A Vector run is drawn by the image model, so it has no model of its
 *  own: on the Vector tab the model pill and the Load / Unload button act on "image" (§31.12.8). */
export type ModelName = "image" | "music";
export type RunStatus = "queued" | "running" | "done" | "failed" | "canceled";
export type WorkerState = "unloaded" | "loading" | "ready" | "busy" | "error" | "unavailable";

/** What an input image is for. Only "reference" is used until local edits (DESIGN.md §21.5). */
export type InputRole = "reference" | "marked" | "mask";

export interface RunOptionsSnapshot {
  width: number | null;
  height: number | null;
  steps: number;
  seed: number;
  seed_was_random: boolean;
  num_images: number;
  negative_prompt: string | null;
  cfg_scale: number | null;
  transparent: boolean;
  draft?: boolean; // a small, quick try (DESIGN.md §22.2); absent on runs made before version 1.3
  full?: FullSize | null; // the size and steps this run stands in for (DESIGN.md §23.1); absent before 1.4, null at full size
  resolution?: number | null; // Edit: 1024 or 2048; null for Generate
  shape_from?: number | null; // Edit with Size on Auto: the 1-based image the result follows, when one was chosen
  roles?: InputRole[]; // the inputs' roles, in order (Edit)
}

export interface FullSize {
  width: number;
  height: number;
  steps: number;
}

/** How a 4K copy was made: `resize` is Make 4K (a standard resize), `model` is Enlarge (an upscaler model, DESIGN.md §28). */
export type FourKMethod = "resize" | "model";

/** The 4K copy of an image, once made (DESIGN.md §27). It is a file beside the image, so it is just a size and a link. */
export interface FourK {
  width: number;
  height: number;
  bytes: number;
  method: FourKMethod;
  url: string;
  download_url: string;
}

/** What Make 4K would make of a picture (DESIGN.md §27.3): its size, and whether a 16:9 (or 9:16) picture is trimmed to the frame. */
export interface FourKSize {
  width: number;
  height: number;
  trimmed: boolean;
}

/** What Enlarge would make of a picture (DESIGN.md §28.2): the size, and how many x2 passes of the model it takes (1 or 2). */
export interface EnlargeSize extends FourKSize {
  passes: number;
}

/** What a picture the studio holds says about Make 4K and Enlarge: a result (ImageInfo) or an edit's source (RunInput) alike. */
export interface FourKTarget {
  id: string;
  can_4k: boolean; // the server's rule for whether Make 4K is offered for this picture
  four_k_size: FourKSize | null; // what it would make; null when it is not offered
  can_enlarge: boolean; // the server's rule for whether Enlarge is offered (whether or not the model is installed)
  enlarge_size: EnlargeSize | null; // what it would make; null when it is not offered
  four_k: FourK | null; // its 4K copy, once made, by either
}

export interface ImageInfo extends FourKTarget {
  idx: number;
  seed: number;
  width: number;
  height: number;
  has_alpha: boolean;
  url: string;
  thumb_url: string | null;
  download_url: string;
}

/** An image an edit was given: its place in the order the model sees them (1 = "image 1"). */
export interface RunInput extends FourKTarget {
  position: number;
  role: InputRole;
  id: string;
  width: number;
  height: number;
  has_alpha: boolean;
  url: string;
  thumb_url: string | null;
}

export type MusicStage = "compose" | "render" | "finish";
/** A Vector run's two stages (DESIGN.md §31.12.5): the model draws the picture, then the clean-up traces it into layers. */
export type VectorStage = "draw" | "trace";

export interface Progress {
  image: number; // the picture (or the track, or the vector version) being made, 1-based
  of: number;
  step: number;
  steps: number;
  stage?: MusicStage | VectorStage; // music: composing (frame by frame), rendering (steps), finishing. Vector: drawing, tracing. Pictures send none.
}

/** A project folder (DESIGN.md §32): a name that runs are filed under, with how many runs of each tab it holds (runs in the bin are not counted). */
export interface Project {
  id: string;
  name: string;
  created_at: string;
  // `vector` is optional because the list of projects was written before Vector (§32.4) and §31.12 does not say whether it counts them: the page
  // treats a missing number as "not known" and never shows it as 0 (see FilterBar and `projectContents`).
  counts: { image: number; music: number; vector?: number };
}

/** What every run has, pictures or music. */
interface RunBase {
  id: string;
  status: RunStatus;
  prompt: string;
  effective_prompt: string;
  model_id: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: { message: string; hint: string | null } | null;
  pinned: boolean; // "Keep": never expires
  expires_at: string | null; // when it will be deleted automatically; null if kept, pending, in the bin, or expiry is off
  deleted_at: string | null; // when it went to the bin (DESIGN.md §30); null if it is not there
  purge_at: string | null; // when it will be deleted for good; null if it is not in the bin
  project_id: string | null; // the project it is filed in (DESIGN.md §32); null if it is in none. A filed run is always kept.
  queue_position: number | null;
  progress: Progress | null;
  canceling: boolean; // running, and the user has asked it to stop (it stops at the next step)
}

/** A result picture that is in the bin on its own (DESIGN.md §33.3): as any picture, with when it went and when it will be deleted for good. */
export interface BinnedImage extends ImageInfo {
  deleted_at: string;
  purge_at: string | null; // null when there is no bin
}

export interface ImageRun extends RunBase {
  mode: ImageMode;
  options: RunOptionsSnapshot;
  lyrics: null;
  inputs: RunInput[]; // an edit's images, in order; empty for Generate
  images: ImageInfo[]; // the result pictures that are NOT in the bin: what the card and the viewer show, numbered by position (§33.1)
  binned_images: BinnedImage[]; // the ones that are; empty for a run that is itself in the bin, whose card shows what it has (§33.2 item 5)
  tracks: [];
  binned_tracks: [];
  vectors: []; // always present, and empty, on a run that is not a Vector run (§31.12.7)
}

/** One finished track of a music run (DESIGN.md §26.3). `seconds` is what the model really made. */
export interface TrackInfo {
  id: string;
  idx: number;
  seed: number;
  seconds: number;
  sample_rate: number;
  channels: number;
  bytes: number;
  url: string;
  download_url: string;
}

/** What a music run used (DESIGN.md §26.5). `fields` is what the page's boxes said, kept for Reuse. */
export interface MusicOptionsSnapshot {
  duration: number;
  steps: number;
  seed: number;
  seed_was_random: boolean;
  tracks: number;
  instrumental: boolean;
  fields: Record<string, string>;
}

/** A track that is in the bin on its own (DESIGN.md §33.3): as any track, with when it went and when it will be deleted for good. */
export interface BinnedTrack extends TrackInfo {
  deleted_at: string;
  purge_at: string | null;
}

export interface MusicRun extends RunBase {
  mode: "music";
  options: MusicOptionsSnapshot;
  lyrics: string | null; // null for an instrumental track
  inputs: [];
  images: [];
  binned_images: [];
  tracks: TrackInfo[]; // the tracks that are not in the bin, numbered by position (DESIGN.md §33.1)
  binned_tracks: BinnedTrack[]; // the ones that are; empty for a run that is itself in the bin
  vectors: [];
}

// ------------------------------------------------------------------ Vector (DESIGN.md §31, version 1.17)
// These shapes are the keys of `backend/tests/vector_contract.json`; `vector.test.tsx` checks that every key of the fixture is in them and the other
// way round, so the page and the server cannot drift apart without a test failing.

export type VectorKind = "silhouette" | "flat" | "line"; // Silhouette or stencil, Flat colour art, Line art (§31.3)
export type VectorLayersBy = "colour" | "shape";
export type VectorArrangement = "side_by_side" | "stacked";

/** What a Vector run asked for, and what the server added when it made it. This is what Reuse and Retry restore (§31.12.6). */
export interface VectorOptionsSnapshot {
  colours: number; // 2 to 8, white included (§31.7 answer 2b)
  palette: string[]; // the INK colours only: uppercase #RRGGBB, never white, at most colours - 1; fewer means the picture chose the rest
  kind: VectorKind;
  width_mm: number;
  height_mm: number;
  layers_by: VectorLayersBy;
  arrangement: VectorArrangement;
  bleed_mm: number;
  min_feature_mm: number;
  kerf_mm: number;
  steps: number;
  seed: number;
  num_images: number; // the versions
  recipe: string; // the words that were sent to the model: the page's edited text, or what the server built
  seed_was_random: boolean;
  negative: string; // the fixed negative text
  draw_width: number; // the size of the picture the model drew, in pixels
  draw_height: number;
}

/** A line the clean-up reports on a version: something removed, or something that needs a look (`code` is for programs, `message` for people). */
export interface VectorNote {
  code: string;
  message: string;
}

/** One layer of a vector, as the check reports it (§31.12.3 item 3). In shape mode `colour` is a label from a fixed list and `picture_colour` is what
 *  the picture had; by colour they are close but not the same. */
export interface VectorLayer {
  index: number; // 1-based; layer 1 has the most ink, so it is the bottom of a stack
  id: string; // layer-2-red
  name: string; // Layer 2 (red)
  colour: string; // #RRGGBB, exact, and the file's own
  picture_colour: string;
  paths: number;
  length_mm: number; // the total cut length of the layer, a rough idea of the time
}

/** The check of one version (§31.4): what the clean-up found and did. */
export interface VectorCheck {
  colours_asked: number;
  colours_found: number; // white included
  white: boolean; // white is among the colours found (it is the bare material and has no layer)
  size_mm: [number, number];
  layers_by: VectorLayersBy;
  arrangement: VectorArrangement;
  svg_bytes: number;
  layers: VectorLayer[];
  smallest_feature_mm: number | null; // the smallest feature that remains; null if the clean-up found none
  removed: { specks: number; thin_parts: number };
  mm_per_pixel: number;
  notes: VectorNote[];
}

/** One finished version of a Vector run. The URLs come from the server and are used as given (the page builds none, except the PNG size). */
export interface VectorItem {
  id: string;
  idx: number; // 0-based place in the run
  seed: number;
  width_mm: number;
  height_mm: number;
  colours: number; // found, white included
  layers: number;
  bytes: number;
  check: VectorCheck;
  url: string; // the combined SVG: never put in the page as markup, only in an <img> or fetched as text to copy
  download_url: string;
  zip_url: string;
  png_url: string;
  png_download_url: string;
  preview_url: string; // a PNG made from the SVG, for the card's <img>
}

export interface VectorRun extends RunBase {
  mode: "vector";
  options: VectorOptionsSnapshot;
  lyrics: null;
  inputs: [];
  images: [];
  binned_images: []; // a Vector run is binned and restored whole: it has no pictures of its own in the bin (§31.12.6)
  tracks: [];
  binned_tracks: [];
  vectors: VectorItem[];
}

export type Run = ImageRun | MusicRun | VectorRun;

export const isMusicRun = (run: Run): run is MusicRun => run.mode === "music";
export const isVectorRun = (run: Run): run is VectorRun => run.mode === "vector";
export const isImageRun = (run: Run): run is ImageRun => run.mode === "generate" || run.mode === "edit";

export interface WorkerStatus {
  state: WorkerState;
  detail: string | null;
  hint: string | null;
  pipeline: string;
  model: ModelName; // the model the worker holds, or last tried to load (DESIGN.md §26.3)
  pid: number | null;
  unload_at: string | null;
  idle_timeout_min: number; // 0 = unload as soon as the queue is empty, so loading ahead of time is not offered (§25)
  device: { name?: string; capability?: string; torch?: string; cuda?: string | null } | null;
  probe: string | null;
}

export interface Status {
  version: string;
  worker: WorkerStatus;
  queue: { running: string | null; queued: number; cap: number; enlarge_waiting: string[] }; // enlarge_waiting: images whose Enlarge is waiting for the GPU (DESIGN.md §28.3)
  memory: { total_gb?: number; available_gb?: number; min_free_gb: number | null; music_min_free_gb: number | null; worker_rss_gb: number | null };
}

export interface Range {
  min: number;
  max: number;
}

/** What the music model needs from the page (DESIGN.md §26.3): the limits the server enforces. */
export interface MusicLimits {
  duration: Range & { default: number }; // seconds, an upper bound: the model may end a piece sooner
  tracks: Range; // versions of one description
  steps: Range & { default: number }; // rendering steps
  description_chars: number;
  lyrics_chars: number;
  field_chars: number;
}

/** Whether the music model can run here, and if not, why (the Music tab explains it). */
export interface MusicAvailability {
  available: boolean;
  state: string | null;
  reason: string | null;
  hint: string | null;
  model: string;
}

/** The preset buttons of the Size control: a machine's bed (§31.12.8). */
export interface VectorPreset {
  id: string;
  name: string;
  width_mm: number;
  height_mm: number;
}

/** What the Vector form needs from the page (§31.12.2): the limits the server enforces, so that the page never hard-codes them. */
export interface VectorLimits {
  colours: Range & { default: number };
  size_mm: Range & { default: [number, number] }; // a side of the sheet, in millimetres
  versions: Range;
  min_feature_mm: Range & { default: number };
  kerf_mm: Range & { default: number };
  bleed_mm: Range & { default: number };
  shape_layer_cap: number; // layers by shape stop here
  kinds: VectorKind[];
  layers_by: VectorLayersBy[];
  arrangements: VectorArrangement[];
  presets: VectorPreset[];
  defaults: { kind: VectorKind; layers_by: VectorLayersBy; arrangement: VectorArrangement };
  recipe_max_chars: number; // the longest edited recipe the server accepts
  // The PNG size range (the long side, in pixels). The contract's `limits_vector` does not carry it (it is in the route's description, §31.12.7: 16 to
  // 8192), so it is optional here: the page uses it if the server ever sends it and VECTOR_PNG_RANGE (vector.ts) otherwise.
  png_px?: Range & { default?: number };
}

/** Whether Vector can run here and, if not, why and what to do (as `MusicAvailability` does for Music). */
export interface VectorAvailability {
  available: boolean;
  reason: string | null;
  hint: string | null;
}

/** Whether Enlarge can run here and, if not, why and what to do (DESIGN.md §28.3). */
export interface UpscalerStatus {
  available: boolean;
  model: string | null;
  reason: string | null;
  hint: string | null;
  max_enlargement: number;
}

export interface Capabilities {
  pipeline: string;
  model: string;
  modes: Mode[];
  supports: { negative_prompt?: boolean; cfg_scale?: boolean; step_progress?: boolean; transparent?: boolean; edit?: boolean; multi_image?: boolean };
  aspect_ratios: Record<string, [number, number]>;
  defaults: {
    mode: ImageMode;
    aspect_ratio: string;
    width: number;
    height: number;
    steps: number;
    seed: number | null;
    num_images: number;
    transparent: boolean;
  };
  limits: {
    prompt_chars: number;
    steps: Range;
    num_images: Range;
    seed: Range;
    cfg_scale: Range;
    size: Range & { multiple: number; max_pixels: number };
    draft: { long_side: number; steps: number }; // what a draft may be: its long side in pixels, and its most steps
    input_images: Range; // how many images one edit may use
    resolutions: number[]; // Edit's 1K / 2K choices (1024, 2048)
    upload_mb: number; // the largest single upload
    edit_warn_units: number; // an edit costing more units than this gets a warning; 0 = never (DESIGN.md §21.4)
    bin_days: number; // how long a deleted run stays in the bin; 0 = there is no bin and Delete is for good (DESIGN.md §30)
    project_name_max: number; // the longest a project's name may be, in characters (DESIGN.md §32.4)
    music: MusicLimits;
    vector: VectorLimits;
  };
  music: MusicAvailability;
  vector: VectorAvailability;
  upscaler: UpscalerStatus;
  queue_cap: number;
  device: WorkerStatus["device"];
}

/** How an edit names one of its images: an upload that is waiting, or an image from an earlier run. */
export type InputRef = ({ upload_id: string } | { image_id: string }) & { role?: InputRole };

/** What POST /api/uploads answers (DESIGN.md §21.6). */
export interface UploadResult {
  upload_id: string;
  width: number;
  height: number;
  has_alpha: boolean;
  bytes: number;
  url: string;
  thumb_url: string | null;
}

export interface CreateRunBody {
  mode: ImageMode;
  prompt: string;
  input_images?: InputRef[]; // Edit only, in the order the model sees them
  options: {
    width: number | null;
    height: number | null;
    steps: number;
    seed: number | null;
    num_images: number;
    negative_prompt: string | null;
    cfg_scale: number | null;
    transparent: boolean;
    draft?: boolean;
    full?: FullSize;
    resolution?: number; // Edit only
    shape_from?: number; // Edit only, with Size on Auto
  };
}

/** A music run (DESIGN.md §26.3). No lyrics means an instrumental track: the server sends the [Instrumental] tag. */
export interface CreateMusicBody {
  mode: "music";
  prompt: string; // the finished description
  lyrics?: string;
  options: {
    duration: number;
    tracks: number;
    steps: number;
    seed: number | null;
    fields: Record<string, string>;
  };
}

/** A Vector run (DESIGN.md §31.12.5): `options` has exactly the keys of the contract's `create_request.options`. `recipe` null means "the server builds it";
 *  a string is the page's edited text. */
export interface CreateVectorBody {
  mode: "vector";
  prompt: string; // the user's idea (the card and the file names use it)
  options: {
    colours: number;
    palette: string[];
    kind: VectorKind;
    width_mm: number;
    height_mm: number;
    layers_by: VectorLayersBy;
    arrangement: VectorArrangement;
    bleed_mm: number;
    min_feature_mm: number;
    kerf_mm: number;
    steps: number;
    seed: number | null;
    num_images: number;
    recipe: string | null;
  };
}

/** The body of `POST /api/vector/recipe` and its answer (§31.12.7): the words the studio will send, for the form to show. Pure text. */
export interface VectorRecipeRequest {
  prompt: string;
  kind: VectorKind;
  colours: number;
  palette: string[];
}
export interface VectorRecipe {
  positive: string;
  negative: string;
}

export interface RunsPage {
  runs: Run[];
  next_before: string | null;
}

/** First event on every /api/events connection: a consistent starting point for the live updates. */
export interface Hello {
  status: Status;
  runs: RunsPage;
}
