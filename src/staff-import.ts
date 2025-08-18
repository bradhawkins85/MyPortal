export interface BasicStaff {
  first_name: string;
  last_name: string;
  email: string | null;
}

export function findExistingStaff<T extends BasicStaff>(
  existingStaff: T[],
  firstName: string,
  lastName: string,
  email: string | null
): T | undefined {
  const emailLower = email?.toLowerCase();
  return existingStaff.find((s) => {
    if (emailLower) {
      return s.email ? s.email.toLowerCase() === emailLower : false;
    }
    return (
      s.first_name.toLowerCase() === firstName.toLowerCase() &&
      s.last_name.toLowerCase() === lastName.toLowerCase()
    );
  });
}
