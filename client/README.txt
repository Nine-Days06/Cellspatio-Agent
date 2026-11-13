CellSpatio portable client

Layout
  launch.cmd     double-click entry point (runs launch.ps1)
  launch.ps1     start the local service and open the browser
  bootstrap.ps1  online package only: install Python deps and check R
  app/           source, skills, frontend build
  runtime/       bundled-runtime package only: python/ (with deps) and R/

Data
  Project data, records and knowledge bases live in <folder>\data.
  Override with CELLSPATIO_HOME. The folder must be writable; do not unzip to
  Program Files. Online and bundled-runtime packages share data only when run
  from the same folder or with the same CELLSPATIO_HOME.

Upgrade
  Extract the new zip over this same folder and overwrite app/, runtime/ and
  the launchers. The zip does not contain data/, so existing projects and
  records stay. Never delete the folder before upgrading; if you move it,
  copy data/ too.

Bundled-runtime package
  Double-click launch.cmd. No login is required.

Online package
  Run bootstrap.ps1 once, then launch.ps1.
  Requires Python 3.10+ (or uv) and R 4.6.x with Seurat, presto and uwot.
  Set R_HOME or add Rscript to PATH (a normal Windows R install is also found
  through the registry).
  R install hint:
    Rscript -e "install.packages(c('Seurat','uwot','remotes'), repos='https://cloud.r-project.org'); remotes::install_github('immunogenomics/presto')"

Offline
  QC through markers runs offline. Knowledge Q&A needs Ollama or an online
  LLM/embedding API. Ollama is not bundled.
