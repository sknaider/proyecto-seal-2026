import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SEAL Studio v2",
  description: "Centro operativo del equipo SEAL",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
