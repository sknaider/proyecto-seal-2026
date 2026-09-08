export const PANEL_SOUL_PATH = "/panel-soul";

// Presentation only. The panel server independently checks the session/role.
export function canOpenPanelSoul(role: string): boolean {
  return role.toLowerCase() === "admin" || role.toLowerCase() === "superuser";
}
