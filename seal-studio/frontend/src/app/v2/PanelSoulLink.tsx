import { ArrowUpRight, SlidersHorizontal } from "lucide-react";
import { canOpenPanelSoul, PANEL_SOUL_PATH } from "@/lib/panelSoul";

export default function PanelSoulLink({ role, compact = false }: { role: string; compact?: boolean }) {
  if (!canOpenPanelSoul(role)) return null;
  return <a href={PANEL_SOUL_PATH} target="_blank" rel="noopener noreferrer"
    className={compact ? "rail-button panel-soul-link" : "channel panel-soul-link"}
    aria-label="Panel SOUL (abre en otra pestaña)" title="Panel SOUL · abre en otra pestaña">
    <SlidersHorizontal size={compact ? 19 : 15} aria-hidden="true"/>
    {!compact && <>Panel SOUL<ArrowUpRight className="nav-chevron" size={14} aria-hidden="true"/></>}
  </a>;
}
