# 0001. Images: WebP on save, a thumbnail for lists, served through CloudFront

Date: 2026-09-05 · Status: accepted

## Context

The weem app was moving ~34 GB/day out of S3 (~$88/month) with almost no
users. Access logs showed the mobile app downloading full-size PNG product
photos (median 327 KB, sub-category tiles 855 KB, up to 9 MB) into 110 px
cards, straight from a public bucket, with no thumbnail and a cache too small
for the catalogue. The image fields resized to 1200x800 but kept the upload
format, so PNG stayed PNG; "quality 90" did nothing for it.

## Decision

1. **One image field for every model**: uploads are re-encoded on save to
   WebP, quality 80, inside 1200x800. Transparency survives; a 2 MB PNG
   becomes ~100 KB. Admins upload whatever they have; the field decides.
2. **Lists serve a thumbnail.** Each list-rendered model gets a 300 px WebP
   rendition generated when the source is saved (imagekit `Optimistic`
   strategy: no storage check on read). List serializers return it under the
   existing `image` key; detail endpoints return the full image. Clients need
   no change.
3. **Cache headers on upload** (7 days) so phones and browsers keep files.
4. **Private bucket behind CloudFront**, media domain from configuration.
   CloudFront's permanent free tier (1 TB/month) covers the volume; the bucket
   is never public.
5. **Existing files are converted once** by an idempotent command that skips
   files already WebP and repoints every row sharing a file. Old objects stay
   in the bucket so cached URLs keep working.

## Consequences

- ~90 % less egress and faster list screens; the S3 line drops from ~$88 to
  under $5/month for weem.
- A new image type (a logo that must stay lossless) needs a deliberate
  exception, not the default.
- Regenerating existing files is a one-off task per app (`regenerate_images`
  on a one-off Fargate task), not a deploy step.
- Reference implementation: weem-backend `common/custom_fields.py`
  (`WebPImageField`, `thumbnail_spec`), `common/serializer_fields.py`
  (`ThumbnailField`), `apps/products/management/commands/regenerate_images.py`.
  The template carries the same field in `apps/common`.
