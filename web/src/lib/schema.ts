import { z } from "zod";

const arxivIdPattern = /^\d{4}\.\d{4,5}$/;
const arxivHtmlPathPattern = /^\/html\/(\d{4}\.\d{4,5})v([1-9]\d*)$/;
const arxivSourcePathPattern = /^\/e-print\/(\d{4}\.\d{4,5})v([1-9]\d*)$/;
const arxivPdfPathPattern = /^\/pdf\/(\d{4}\.\d{4,5})v([1-9]\d*)$/;
const cachedFigurePathPattern =
  /^\/figures\/(\d{4}\.\d{4,5})\/v([1-9]\d*)\/fig([12])-panel([1-9]\d*)\.(png|jpg|webp|gif|svg)$/;
const allowedArxivHosts = new Set(["arxiv.org", "www.arxiv.org"]);
const persistedBoundaryWhitespacePattern =
  /^[\u0009-\u000D\u001C-\u001F\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\uFEFF]+|[\u0009-\u000D\u001C-\u001F\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\uFEFF]+$/gu;

const nonBlankString = z
  .string()
  .transform((value) => value.replace(persistedBoundaryWhitespacePattern, ""))
  .pipe(z.string().min(1));
const normalizedNonBlankString = nonBlankString;
const nonBlankStringList = z.array(nonBlankString).min(1);
const utcDatetimeSchema = z.iso
  .datetime()
  .refine((value) => value.endsWith("Z"), "Datetime must be normalized to UTC");
const nonNegativeInteger = z.number().int().nonnegative();
const counter = () => nonNegativeInteger.default(0);
const httpUrlSchema = z.url().refine((value) => {
  const protocol = new URL(value).protocol;
  return protocol === "http:" || protocol === "https:";
}, "URL must use HTTP or HTTPS");

export const topicSchema = z.enum([
  "VLA",
  "WAM",
  "World Model",
  "Dataset",
  "Benchmark",
]);

export const tagSchema = z.enum([
  "Action Prediction",
  "Data",
  "Evaluation",
  "Generalist Robotics",
  "Policy Learning",
  "Robot Learning",
  "Robot Manipulation",
  "Simulation",
  "Video Generation",
  "Vision-Language",
  "World Modeling",
]);

function parseArxivUrl(
  value: string,
  context: z.RefinementCtx,
  kind: "html" | "image",
): URL | null {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    context.addIssue({ code: "custom", message: "Figure URL must be valid" });
    return null;
  }
  if (
    url.protocol !== "https:" ||
    !allowedArxivHosts.has(url.hostname) ||
    url.username ||
    url.password ||
    url.port
  ) {
    context.addIssue({
      code: "custom",
      message:
        "Figure URL must use HTTPS on arxiv.org without credentials or a custom port",
    });
  }
  if (url.hash) {
    context.addIssue({
      code: "custom",
      message: "Figure URL must not contain a fragment",
    });
  }
  if (kind === "html" && url.search) {
    context.addIssue({
      code: "custom",
      message: "Figure HTML URL must not contain a query",
    });
  }
  return url;
}

const arxivHtmlUrlSchema = z.url().superRefine((value, context) => {
  const url = parseArxivUrl(value, context, "html");
  if (url && !arxivHtmlPathPattern.test(url.pathname)) {
    context.addIssue({
      code: "custom",
      message: "Figure HTML URL must identify an arXiv paper id and version",
    });
  }
});

const arxivImageUrlSchema = z.url().superRefine((value, context) => {
  const url = parseArxivUrl(value, context, "image");
  if (url && !/^\/html\/\d{4}\.\d{4,5}v[1-9]\d*\/.+$/.test(url.pathname)) {
    context.addIssue({
      code: "custom",
      message: "Figure image URL must identify an arXiv paper id and version",
    });
  }
});

function parseFigureSourceIdentity(
  value: string,
  source: "arxiv_html" | "arxiv_source" | "arxiv_pdf",
  context: z.RefinementCtx,
): RegExpExecArray | null {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    context.addIssue({ code: "custom", message: "Figure URL must be valid" });
    return null;
  }
  if (
    url.protocol !== "https:" ||
    !allowedArxivHosts.has(url.hostname) ||
    url.username ||
    url.password ||
    url.port
  ) {
    context.addIssue({
      code: "custom",
      message:
        "Figure URL must use HTTPS on arxiv.org without credentials or a custom port",
    });
  }
  if (url.search) {
    context.addIssue({
      code: "custom",
      message: "Figure source URL must not contain a query",
    });
  }
  if (source === "arxiv_html") {
    if (url.hash.length < 2) {
      context.addIssue({
        code: "custom",
        message: "Figure source URL must contain a non-empty fragment",
      });
    }
  } else if (url.hash) {
    context.addIssue({
      code: "custom",
      message: "Recovered Figure source URL must not contain a fragment",
    });
  }
  const pattern =
    source === "arxiv_html"
      ? arxivHtmlPathPattern
      : source === "arxiv_source"
        ? arxivSourcePathPattern
        : arxivPdfPathPattern;
  const identity = pattern.exec(url.pathname);
  if (!identity) {
    context.addIssue({
      code: "custom",
      message: `${source} URL must identify an arXiv paper id and version`,
    });
  }
  return identity;
}

export const figureAssetSchema = z
  .object({
    number: z.union([z.literal(1), z.literal(2)]),
    label: nonBlankString,
    caption: nonBlankString,
    image_urls: z.array(arxivImageUrlSchema.nullable()).min(1),
    cached_image_paths: z.array(z.string().nullable()).default([]),
    source_url: z.url(),
    source: z.enum(["arxiv_html", "arxiv_source", "arxiv_pdf"]),
  })
  .strict()
  .superRefine((asset, context) => {
    const sourceIdentity = parseFigureSourceIdentity(
      asset.source_url,
      asset.source,
      context,
    );
    if (
      asset.cached_image_paths.length > 0 &&
      asset.cached_image_paths.length !== asset.image_urls.length
    ) {
      context.addIssue({
        code: "custom",
        path: ["cached_image_paths"],
        message: "Cached Figure paths must align with remote image panels",
      });
      return;
    }
    const cachedPaths =
      asset.cached_image_paths.length > 0
        ? asset.cached_image_paths
        : asset.image_urls.map(() => null);
    if (
      asset.source === "arxiv_html" &&
      asset.image_urls.some((imageUrl) => imageUrl === null)
    ) {
      context.addIssue({
        code: "custom",
        path: ["image_urls"],
        message: "arXiv HTML Figure panels require remote image URLs",
      });
    }
    if (
      asset.source !== "arxiv_html" &&
      asset.image_urls.some((imageUrl) => imageUrl !== null)
    ) {
      context.addIssue({
        code: "custom",
        path: ["image_urls"],
        message: "Recovered Figure panels must be local-only",
      });
    }
    for (const [index, path] of cachedPaths.entries()) {
      if (asset.image_urls[index] === null && path === null) {
        context.addIssue({
          code: "custom",
          path: ["image_urls", index],
          message: "Each Figure panel requires a remote URL or cached path",
        });
      }
      if (path === null) continue;
      const match = cachedFigurePathPattern.exec(path);
      if (
        !match ||
        !sourceIdentity ||
        match[1] !== sourceIdentity[1] ||
        match[2] !== sourceIdentity[2] ||
        Number.parseInt(match[3] ?? "", 10) !== asset.number ||
        Number.parseInt(match[4] ?? "", 10) !== index + 1
      ) {
        context.addIssue({
          code: "custom",
          path: ["cached_image_paths", index],
          message: "Cached Figure path must match its paper and panel",
        });
      }
    }
  })
  .transform((asset) => {
    const image_urls: Array<string | null> = [];
    const cached_image_paths: Array<string | null> = [];
    const seen = new Set<string>();
    const cachedPaths =
      asset.cached_image_paths.length > 0
        ? asset.cached_image_paths
        : asset.image_urls.map(() => null);
    for (const [index, imageUrl] of asset.image_urls.entries()) {
      if (imageUrl !== null) {
        if (seen.has(imageUrl)) continue;
        seen.add(imageUrl);
      }
      image_urls.push(imageUrl);
      cached_image_paths.push(cachedPaths[index] ?? null);
    }
    return {
      ...asset,
      image_urls,
      cached_image_paths,
    };
  });

export const figureGallerySchema = z
  .object({
    status: z.enum([
      "available",
      "html_unavailable",
      "not_found",
      "fetch_failed",
    ]),
    html_url: arxivHtmlUrlSchema,
    figures: z.array(figureAssetSchema).max(2),
    checked_at: utcDatetimeSchema,
    recovery_status: z
      .enum(["not_attempted", "available", "not_found", "fetch_failed"])
      .optional(),
    recovery_checked_at: utcDatetimeSchema.nullable().default(null),
    recovery_version: nonNegativeInteger.default(0),
  })
  .strict()
  .superRefine((gallery, context) => {
    const numbers = gallery.figures.map((figure) => figure.number);
    if (new Set(numbers).size !== numbers.length) {
      context.addIssue({
        code: "custom",
        message: "Figure numbers must be unique",
      });
    }
    if (gallery.status === "available" && gallery.figures.length === 0) {
      context.addIssue({
        code: "custom",
        message: "Available Figure gallery must contain at least one figure",
      });
    }
    if (gallery.status !== "available" && gallery.figures.length > 0) {
      context.addIssue({
        code: "custom",
        message: "Unavailable Figure gallery must not contain figures",
      });
    }
    const hasFigureOne = gallery.figures.some((figure) => figure.number === 1);
    const recoveryStatus =
      gallery.recovery_status ?? (hasFigureOne ? "available" : "not_attempted");
    if (recoveryStatus === "available" && !hasFigureOne) {
      context.addIssue({
        code: "custom",
        path: ["recovery_status"],
        message: "Available Figure recovery requires Figure 1",
      });
    }
    if (
      (recoveryStatus === "not_found" || recoveryStatus === "fetch_failed") &&
      gallery.recovery_checked_at === null
    ) {
      context.addIssue({
        code: "custom",
        path: ["recovery_checked_at"],
        message: "Terminal Figure recovery requires a checked timestamp",
      });
    }

    const galleryUrl = new URL(gallery.html_url);
    for (const [figureIndex, figure] of gallery.figures.entries()) {
      const sourcePath = new URL(figure.source_url).pathname;
      const sourcePattern =
        figure.source === "arxiv_html"
          ? arxivHtmlPathPattern
          : figure.source === "arxiv_source"
            ? arxivSourcePathPattern
            : arxivPdfPathPattern;
      const sourceIdentity = sourcePattern.exec(sourcePath);
      const galleryIdentity = arxivHtmlPathPattern.exec(galleryUrl.pathname);
      if (
        !sourceIdentity ||
        !galleryIdentity ||
        sourceIdentity[1] !== galleryIdentity[1] ||
        sourceIdentity[2] !== galleryIdentity[2]
      ) {
        context.addIssue({
          code: "custom",
          path: ["figures", figureIndex, "source_url"],
          message: "Figure source paper id and version must match the gallery",
        });
      }
      for (const [imageIndex, image] of figure.image_urls.entries()) {
        if (image === null) continue;
        const imageUrl = new URL(image);
        if (!imageUrl.pathname.startsWith(`${galleryUrl.pathname}/`)) {
          context.addIssue({
            code: "custom",
            path: ["figures", figureIndex, "image_urls", imageIndex],
            message: "Figure image paper id and version must match the gallery",
          });
        }
      }
    }
  })
  .transform((gallery) => ({
    ...gallery,
    recovery_status:
      gallery.recovery_status ??
      (gallery.figures.some((figure) => figure.number === 1)
        ? ("available" as const)
        : ("not_attempted" as const)),
    figures: [...gallery.figures].sort(
      (left, right) => left.number - right.number,
    ),
  }));

export const analysisSchema = z
  .object({
    relevance_score: z.number().int().min(1).max(10),
    primary_topic: topicSchema,
    tags: z.array(tagSchema),
    one_sentence_summary: normalizedNonBlankString,
    main_contribution: normalizedNonBlankString,
    method: normalizedNonBlankString,
    key_results: normalizedNonBlankString,
    limitations: normalizedNonBlankString,
    relation_to_vla_wam: normalizedNonBlankString,
  })
  .strict()
  .transform((analysis) => ({
    ...analysis,
    tags: [...new Set(analysis.tags)],
  }));

export const resourcesSchema = z
  .object({
    arxiv_url: httpUrlSchema,
    pdf_url: httpUrlSchema,
    project_url: httpUrlSchema.nullable(),
    code_url: httpUrlSchema.nullable(),
  })
  .strict();

export const provenanceSchema = z
  .object({
    analysis_scope: z.literal("title_and_abstract"),
    model: normalizedNonBlankString,
    prompt_version: normalizedNonBlankString,
    analyzed_at: utcDatetimeSchema,
  })
  .strict();

export const paperSchema = z
  .object({
    arxiv_id: z.string().regex(arxivIdPattern),
    version: z.number().int().positive(),
    published_at: utcDatetimeSchema,
    updated_at: utcDatetimeSchema,
    title: nonBlankString,
    title_zh: normalizedNonBlankString,
    authors: nonBlankStringList,
    arxiv_categories: nonBlankStringList,
    abstract: nonBlankString,
    matched_rules: nonBlankStringList,
    analysis: analysisSchema,
    resources: resourcesSchema,
    provenance: provenanceSchema,
    figure_gallery: figureGallerySchema,
  })
  .strict()
  .superRefine((paper, context) => {
    const match = arxivHtmlPathPattern.exec(
      new URL(paper.figure_gallery.html_url).pathname,
    );
    if (
      !match ||
      match[1] !== paper.arxiv_id ||
      Number.parseInt(match[2] ?? "", 10) !== paper.version
    ) {
      context.addIssue({
        code: "custom",
        path: ["figure_gallery", "html_url"],
        message: "Paper and Figure gallery identity must match",
      });
    }
  });

export const runStatsSchema = z
  .object({
    fetched: counter(),
    prefiltered: counter(),
    cache_hits: counter(),
    figure_cache_hits: counter(),
    figure_requests: counter(),
    figure_available: counter(),
    figure_unavailable: counter(),
    figure_failed: counter(),
    model_calls: counter(),
    published: counter(),
    failed: counter(),
    prompt_tokens: counter(),
    completion_tokens: counter(),
    total_tokens: counter(),
    error_categories: z.record(z.string(), nonNegativeInteger).default({}),
  })
  .strict()
  .superRefine((stats, context) => {
    if (stats.total_tokens !== stats.prompt_tokens + stats.completion_tokens) {
      context.addIssue({
        code: "custom",
        path: ["total_tokens"],
        message:
          "Total token count must equal prompt plus completion token counts",
      });
    }
  });

export const dataFileSchema = z
  .object({
    schema_version: z.literal("1"),
    generated_at: utcDatetimeSchema,
    stats: runStatsSchema,
    papers: z.array(paperSchema),
  })
  .strict();

export type Topic = z.infer<typeof topicSchema>;
export type FigureGallery = z.infer<typeof figureGallerySchema>;
export type Paper = z.infer<typeof paperSchema>;
export type RunStats = z.infer<typeof runStatsSchema>;
export type DataFile = z.infer<typeof dataFileSchema>;
