CTBids Sale Suite download
==========================

Put the built zip here so /desktop and the Tools page can serve it:

  CTBids-Sale-Suite.zip

Build it from the "coins ale" project:

  build_suite.bat

Then copy:

  coins ale\dist\CTBids-Sale-Suite.zip
  →  CaringTransitions_AI\frontend\public\downloads\CTBids-Sale-Suite.zip

For production (Railway / Vercel), prefer hosting the zip on OneDrive,
Cloudflare R2, or S3 and set:

  NEXT_PUBLIC_SALE_SUITE_DOWNLOAD_URL=https://your-host/CTBids-Sale-Suite.zip

Do not commit a huge zip to git if you can avoid it.
