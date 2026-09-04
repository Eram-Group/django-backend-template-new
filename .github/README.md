# backend-template

The company's Django backend starting point, packaged as a
[Copier](https://copier.readthedocs.io) template. Generate a project:

```bash
uvx copier copy gh:Eram-Group/backend-template my-app
```

Copier asks for the product name, the AWS app slug, the GitHub repo, the
domain under `eramapps.com`, **PostgreSQL or PostGIS** (PostGIS adds
GeoDjango and the `zones` app), the brand colour and icon, the sender and
superuser emails, the team timezone and a random admin path - then writes a
project that passes every gate as-is (`just bootstrap`, `just gates`).

Projects keep `.copier-answers.yml`; `uvx copier update` pulls later
template improvements (tags mark releases).

What the generated project is - architecture, layering, deploy shape - is
documented inside it (`README.md`, `docs/ARCHITECTURE.md`,
`docs/AWS_ARCHITECTURE.md`, `docs/DEPLOYMENT.md`, `docs/NEW_PROJECT.md`).
How to work on the template itself: [docs/TEMPLATE.md](docs/TEMPLATE.md).
CI (`.github/workflows/template.yml`) generates every preset in `presets/`
and runs the generated project's gates on it.
