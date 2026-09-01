import { redirect } from "next/navigation";

/** The guide opens on the page that says what this is. Nothing is gained by an index
 *  of an index. */
export default function DocsIndex() {
  redirect("/docs/index");
}
