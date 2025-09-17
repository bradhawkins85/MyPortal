export interface TemplateVariableDefinition {
  key: string;
  description: string;
  resolve: (context: TemplateContext) => string | number | null | undefined;
}

export interface TemplateContextUser {
  id?: number;
  email?: string | null;
  firstName?: string | null;
  lastName?: string | null;
}

export interface TemplateContextCompany {
  id?: number | null;
  name?: string | null;
  syncroCustomerId?: string | null;
}

export interface TemplateContextPortal {
  baseUrl?: string | null;
  loginUrl?: string | null;
}

export interface TemplateContext {
  user?: TemplateContextUser;
  company?: TemplateContextCompany;
  portal?: TemplateContextPortal;
}

export const TEMPLATE_VARIABLES: TemplateVariableDefinition[] = [
  {
    key: 'user.email',
    description: 'Email address of the logged-in user.',
    resolve: (context) => context.user?.email ?? '',
  },
  {
    key: 'user.firstName',
    description: 'First name of the logged-in user.',
    resolve: (context) => context.user?.firstName ?? '',
  },
  {
    key: 'user.lastName',
    description: 'Last name of the logged-in user.',
    resolve: (context) => context.user?.lastName ?? '',
  },
  {
    key: 'user.fullName',
    description: 'Full name of the logged-in user.',
    resolve: (context) => {
      const first = context.user?.firstName?.trim() ?? '';
      const last = context.user?.lastName?.trim() ?? '';
      return [first, last].filter(Boolean).join(' ').trim();
    },
  },
  {
    key: 'company.id',
    description: 'Numeric identifier of the active company.',
    resolve: (context) =>
      context.company?.id !== undefined && context.company?.id !== null
        ? String(context.company.id)
        : '',
  },
  {
    key: 'company.name',
    description: 'Name of the active company.',
    resolve: (context) => context.company?.name ?? '',
  },
  {
    key: 'company.syncroId',
    description: 'Syncro customer identifier for the active company, when available.',
    resolve: (context) => context.company?.syncroCustomerId ?? '',
  },
  {
    key: 'portal.baseUrl',
    description: 'Base URL for the MyPortal instance.',
    resolve: (context) => context.portal?.baseUrl ?? '',
  },
  {
    key: 'portal.loginUrl',
    description: 'Login URL for the MyPortal instance.',
    resolve: (context) => context.portal?.loginUrl ?? '',
  },
];

export interface TemplateReplacementMap {
  [placeholder: string]: string;
}

export function buildTemplateReplacementMap(context: TemplateContext): TemplateReplacementMap {
  const replacements: TemplateReplacementMap = {};
  for (const variable of TEMPLATE_VARIABLES) {
    const rawValue = variable.resolve(context);
    const stringValue = rawValue === undefined || rawValue === null ? '' : String(rawValue);
    const token = `{{${variable.key}}}`;
    const encodedValue = encodeURIComponent(stringValue);

    replacements[token] = stringValue;
    replacements[`${token}UrlEncoded`] = encodedValue;
    replacements[`{{${variable.key}UrlEncoded}}`] = encodedValue;
  }
  return replacements;
}

export function applyTemplateVariables(value: string, replacements: TemplateReplacementMap): string {
  let result = value;
  const ordered = Object.entries(replacements).sort(
    ([tokenA], [tokenB]) => tokenB.length - tokenA.length
  );
  for (const [token, replacement] of ordered) {
    if (!token) continue;
    result = result.split(token).join(replacement);
  }
  return result;
}
