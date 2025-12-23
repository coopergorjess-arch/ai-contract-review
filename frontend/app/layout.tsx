// frontend/app/layout.tsx
import "./globals.css";

export const metadata = {
  title: "AI Contract Review",
  description: "Contract review UI",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">{children}</body>
    </html>
  );
}