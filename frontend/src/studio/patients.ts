// Synthetic patients from the seeded demo database (default anchor 2026-11-16);
// each has a future booked appointment. The seed is to guarantee them (task EN-05).
export const STUDIO_PATIENTS = {
  p0097: { firstName: "Miguel", lastName1: "Núñez", lastName2: "Pérez", dob: "1977-05-06" },
  p0181: { firstName: "Isabel", lastName1: "Ramírez", lastName2: "Jiménez", dob: "1984-06-20" },
  p0042: { firstName: "Yolanda", lastName1: "Calvo", lastName2: "Suárez", dob: "2001-11-14" },
  p0157: { firstName: "Carlos", lastName1: "Hernández", lastName2: "Martín", dob: "1997-10-12" },
} as const;