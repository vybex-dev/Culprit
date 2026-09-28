import { JobPageClient } from "./JobPageClient";

export default async function JobPage({
  params,
}: {
  params: Promise<{ jobId: string }>;
}) {
  const { jobId } = await params;
  return <JobPageClient jobId={jobId} />;
}
