import Link from "next/link";

// Phase 0 hello-world shell. Real surfaces are specified in 02-UIUX_SPEC and land in Phase 3+.
const surfaces = [
  { href: "/student", label: "Student", note: "Today · Week · Zuzu · Spike · Opportunities · Me" },
  { href: "/parent", label: "Parent", note: "Family Home · Outcomes · Funding · Approvals · Trust Center" },
  { href: "/coach", label: "Coach", note: "Escalations · Approvals · Roster · Student Detail" },
  { href: "/admin", label: "Admin", note: "Cohort health · Coach load · Model spend · Eval registry" },
];

export default function Home() {
  return (
    <main>
      <h1>Spykt</h1>
      <p>Phase 0 skeleton — deployable shell, no product surfaces yet.</p>
      <ul>
        {surfaces.map((s) => (
          <li key={s.href}>
            <Link href={s.href}>{s.label}</Link> — {s.note}
          </li>
        ))}
      </ul>
    </main>
  );
}
