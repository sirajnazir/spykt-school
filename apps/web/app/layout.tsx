import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Spykt",
  description: "Autonomous prep institution — humans in the loop at the moments that matter.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: 0, padding: "2rem" }}>{children}</body>
    </html>
  );
}
