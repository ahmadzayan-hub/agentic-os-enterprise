export const metadata = {
  title: 'Agentic OS Enterprise',
  description: 'Governed enterprise AI control and intelligence platform'
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
