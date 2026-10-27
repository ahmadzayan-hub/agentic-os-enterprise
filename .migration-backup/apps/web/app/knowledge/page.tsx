import { KnowledgeSearch } from "@/components/knowledge-search";

export default function KnowledgePage() {
  return (
    <div className="stack">
      <div>
        <h1>Knowledge</h1>
        <p className="page-lede">
          Retrieval is authorised before ranking, not filtered afterwards. Content
          you cannot read is never fetched, never scored and never reaches a model
          context.
        </p>
      </div>
      <KnowledgeSearch />
    </div>
  );
}
