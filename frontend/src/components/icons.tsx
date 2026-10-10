// Small inline icons (no icon font or CDN: the CSP allows same-origin assets only).
import type { SVGProps } from "react";

function Icon({ children, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" {...props}>
      {children}
    </svg>
  );
}

export const SparkleIcon = () => (
  <Icon><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" /><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z" /></Icon>
);
export const SlidersIcon = () => (
  <Icon><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0" /><circle cx="16" cy="6" r="2" /><circle cx="10" cy="12" r="2" /><circle cx="18" cy="18" r="2" /></Icon>
);
export const SunIcon = () => (
  <Icon><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></Icon>
);
export const MoonIcon = () => <Icon><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></Icon>;
export const MonitorIcon = () => <Icon><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></Icon>;
export const CloseIcon = () => <Icon><path d="M6 6l12 12M18 6L6 18" /></Icon>;
export const ReuseIcon = () => <Icon><path d="M3 12a9 9 0 0 1 15.5-6.2L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-15.5 6.2L3 16" /><path d="M3 21v-5h5" /></Icon>;
export const DownloadIcon = () => <Icon><path d="M12 4v11M7 10l5 5 5-5M5 20h14" /></Icon>;
export const CopyIcon = () => <Icon><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h8" /></Icon>;
// a circular arrow going back: the icon of Restore
export const RestoreIcon = () => <Icon><path d="M3 12a9 9 0 1 0 3-6.7" /><path d="M3 4v5h5" /></Icon>;
export const TrashIcon = () => <Icon><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></Icon>;
export const EditIcon = () => <Icon><path d="M4 20h4L19 9l-4-4L4 16z" /><path d="M14 6l4 4" /></Icon>;
export const DraftIcon = () => <Icon><path d="M13 3L5 14h6l-1 7 8-11h-6z" /></Icon>;
export const EnlargeIcon = () => <Icon><path d="M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7" /></Icon>;
export const UpscaleIcon = () => <Icon><rect x="3" y="11" width="10" height="10" rx="1.5" /><path d="M13 3h8v8M21 3l-8 8" /></Icon>;
export const ZoomInIcon = () => <Icon><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5L21 21M10.5 8v5M8 10.5h5" /></Icon>;
export const PlusIcon = () => <Icon><path d="M12 5v14M5 12h14" /></Icon>;
export const GripIcon = () => <Icon><circle cx="9" cy="6" r="1.2" fill="currentColor" /><circle cx="15" cy="6" r="1.2" fill="currentColor" /><circle cx="9" cy="12" r="1.2" fill="currentColor" /><circle cx="15" cy="12" r="1.2" fill="currentColor" /><circle cx="9" cy="18" r="1.2" fill="currentColor" /><circle cx="15" cy="18" r="1.2" fill="currentColor" /></Icon>;
export const StopIcon = () => <Icon><rect x="6" y="6" width="12" height="12" rx="2" /></Icon>;
export const PinIcon = () => <Icon><path d="M7 4h10v17l-5-4-5 4z" /></Icon>;
export const ChevronLeft = () => <Icon><path d="M15 6l-6 6 6 6" /></Icon>;
export const ChevronRight = () => <Icon><path d="M9 6l6 6-6 6" /></Icon>;
export const DiceIcon = () => <Icon><rect x="4" y="4" width="16" height="16" rx="3" /><circle cx="9" cy="9" r="1" fill="currentColor" /><circle cx="15" cy="15" r="1" fill="currentColor" /><circle cx="15" cy="9" r="1" fill="currentColor" /><circle cx="9" cy="15" r="1" fill="currentColor" /></Icon>;
export const AlertIcon = () => <Icon><path d="M12 3l9 16H3z" /><path d="M12 10v4M12 17h0" /></Icon>;
export const ChipIcon = () => (
  <Icon><rect x="7" y="7" width="10" height="10" rx="2" /><path d="M9 3v2M15 3v2M9 19v2M15 19v2M3 9h2M3 15h2M19 9h2M19 15h2" /></Icon>
);
export const EjectIcon = () => <Icon><path d="M12 5l7 8H5z" /><path d="M5 18h14" /></Icon>;
export const NoteIcon = () => <Icon><path d="M9 18V5l11-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="17" cy="16" r="3" /></Icon>;
// a folder: the icon of a project (DESIGN.md §32)
export const FolderIcon = () => <Icon><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></Icon>;
export const ChevronDownIcon = () => <Icon><path d="M6 9l6 6 6-6" /></Icon>;
export const CheckIcon = () => <Icon><path d="M5 12.5l4.5 4.5L19 7.5" /></Icon>;
