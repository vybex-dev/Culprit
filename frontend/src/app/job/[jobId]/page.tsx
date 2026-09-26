// FILE: frontend/src/app/job/[jobId]/page.tsx — place at this path in the Culprit repo

import { JobPageClient } from "./JobPageClient";

export default async function JobPage({ params }: PageProps<"/job/[jobId]">) {
  const { jobId } = await params;
  return <JobPageClient jobId={jobId} />;
}
