import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "VidSOPEngine",
    short_name: "VidSOPEngine",
    description: "Run SOPs, workflows, checklists, training, documents, and daily operations.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait-primary",
    background_color: "#f8fafc",
    theme_color: "#0f172a",
    categories: ["business", "productivity"],
    icons: [
      // Square brand mark — PWA install dialog + Android splash
      // screens pick whichever fits. logo-single is the square-only
      // version of the lockup.
      {
        src: "/logo-single.png",
        sizes: "any",
        type: "image/png",
        purpose: "any",
      },
      // Existing PNG home-screen icons kept as-is — they're already
      // sized for the OS-mandated 192 / 512 buckets, and "maskable"
      // requires a fixed pixel size. Replace the source PNGs at
      // public/icons/ to refresh them with the new mark.
      {
        src: "/icons/icon-192.png",
        sizes: "192x192",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icons/icon-192.png",
        sizes: "192x192",
        type: "image/png",
        purpose: "maskable",
      },
      {
        src: "/icons/icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icons/icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
    shortcuts: [
      {
        name: "Tasks",
        short_name: "Tasks",
        description: "Open today and overdue work",
        url: "/",
        icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }],
      },
      {
        name: "SOPs",
        short_name: "SOPs",
        description: "Open the SOP library",
        url: "/sops",
        icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }],
      },
    ],
  };
}
