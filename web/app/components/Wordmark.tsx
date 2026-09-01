/**
 * The LanceDB lockup, painted rather than drawn.
 *
 * The supplied asset is a single-colour PNG at #f4ebe8 — made for a near-black
 * background, and invisible on a light one. Rather than ship a second bitmap and
 * pick between them, the PNG is used as a mask and filled with the current theme's
 * heading colour, so it is correct in both themes by construction and cannot drift
 * out of step with the palette.
 *
 * Aspect ratio is the asset's own, 390:91.
 */
const RATIO = 390 / 91;

export default function Wordmark({ height = 19 }: { height?: number }) {
  const url = "url(/brand/lancedb-wordmark.png)";
  return (
    <span
      role="img"
      aria-label="LanceDB"
      className="block shrink-0"
      style={{
        height,
        width: Math.round(height * RATIO),
        backgroundColor: "var(--bright)",
        maskImage: url,
        maskSize: "contain",
        maskRepeat: "no-repeat",
        maskPosition: "center",
        WebkitMaskImage: url,
        WebkitMaskSize: "contain",
        WebkitMaskRepeat: "no-repeat",
        WebkitMaskPosition: "center",
      }}
    />
  );
}
