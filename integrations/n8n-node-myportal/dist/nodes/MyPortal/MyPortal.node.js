"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MyPortal = void 0;
const n8n_workflow_1 = require("n8n-workflow");
function compactObject(values) {
    return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined && value !== null && value !== ''));
}
function parseJsonObject(value, fieldName) {
    if (!value.trim())
        return undefined;
    const parsed = JSON.parse(value);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new n8n_workflow_1.NodeOperationError({ name: 'MyPortal' }, `${fieldName} must be a JSON object`);
    }
    return parsed;
}
function normaliseBaseUrl(baseUrl) {
    return baseUrl.replace(/\/+$/, '');
}
class MyPortal {
    constructor() {
        this.description = {
            displayName: 'MyPortal',
            name: 'myPortal',
            icon: 'file:myportal.svg',
            group: ['transform'],
            version: 1,
            subtitle: '={{$parameter["resource"] + ": " + $parameter["operation"]}}',
            description: 'Create, get, get all, update, and delete MyPortal staff and tickets',
            defaults: { name: 'MyPortal' },
            inputs: [n8n_workflow_1.NodeConnectionTypes.Main],
            outputs: [n8n_workflow_1.NodeConnectionTypes.Main],
            credentials: [{ name: 'myPortalApi', required: true }],
            usableAsTool: true,
            properties: [
                {
                    displayName: 'Resource',
                    name: 'resource',
                    noDataExpression: true,
                    type: 'options',
                    default: 'ticket',
                    options: [
                        { name: 'Staff', value: 'staff' },
                        { name: 'Ticket', value: 'ticket' },
                    ],
                },
                {
                    displayName: 'Operation',
                    name: 'operation',
                    noDataExpression: true,
                    type: 'options',
                    default: 'getAll',
                    options: [
                        { name: 'Create', value: 'create', action: 'Create a record' },
                        { name: 'Delete', value: 'delete', action: 'Delete a record' },
                        { name: 'Get', value: 'get', action: 'Get a record' },
                        { name: 'Get Many', value: 'getAll', action: 'Get many records' },
                        { name: 'Update', value: 'update', action: 'Update a record' },
                    ],
                },
                {
                    displayName: 'ID', name: 'id', type: 'number', default: 0, required: true,
                    displayOptions: { show: { operation: ['get', 'update', 'delete'] } },
                },
                {
                    displayName: 'Company ID', name: 'companyId', type: 'number', default: 0,
                    displayOptions: { show: { resource: ['staff'], operation: ['create', 'getAll'] } },
                    description: 'Required to create staff; optional filter for Get All',
                },
                { displayName: 'First Name', name: 'firstName', type: 'string', default: '', displayOptions: { show: { resource: ['staff'], operation: ['create', 'update'] } } },
                { displayName: 'Last Name', name: 'lastName', type: 'string', default: '', displayOptions: { show: { resource: ['staff'], operation: ['create', 'update'] } } },
                { displayName: 'Email', name: 'email', type: 'string', default: '', placeholder: 'name@email.com', displayOptions: { show: { resource: ['staff'], operation: ['create', 'update', 'getAll'] } } },
                { displayName: 'Mobile Phone', name: 'mobilePhone', type: 'string', default: '', displayOptions: { show: { resource: ['staff'], operation: ['create', 'update'] } } },
                { displayName: 'Enabled', name: 'enabled', type: 'boolean', default: true, displayOptions: { show: { resource: ['staff'], operation: ['create', 'update'] } } },
                { displayName: 'Department', name: 'department', type: 'string', default: '', displayOptions: { show: { resource: ['staff'], operation: ['create', 'update'] } } },
                { displayName: 'Job Title', name: 'jobTitle', type: 'string', default: '', displayOptions: { show: { resource: ['staff'], operation: ['create', 'update'] } } },
                { displayName: 'Account Action', name: 'accountAction', type: 'string', default: '', displayOptions: { show: { resource: ['staff'], operation: ['create', 'update', 'getAll'] } } },
                { displayName: 'Custom Fields JSON', name: 'customFieldsJson', type: 'json', default: '{}', displayOptions: { show: { resource: ['staff'], operation: ['create', 'update'] } } },
                { displayName: 'Subject', name: 'subject', type: 'string', default: '', displayOptions: { show: { resource: ['ticket'], operation: ['create', 'update'] } } },
                { displayName: 'Description', name: 'description', type: 'string', typeOptions: { rows: 4 }, default: '', displayOptions: { show: { resource: ['ticket'], operation: ['create', 'update'] } } },
                { displayName: 'Status', name: 'status', type: 'string', default: '', displayOptions: { show: { resource: ['ticket'], operation: ['create', 'update', 'getAll'] } } },
                { displayName: 'Priority', name: 'priority', type: 'string', default: 'normal', displayOptions: { show: { resource: ['ticket'], operation: ['create', 'update'] } } },
                { displayName: 'Requester ID', name: 'requesterId', type: 'number', default: 0, displayOptions: { show: { resource: ['ticket'], operation: ['create'] } }, description: 'Required by MyPortal when using API key authentication' },
                { displayName: 'Company ID', name: 'ticketCompanyId', type: 'number', default: 0, displayOptions: { show: { resource: ['ticket'], operation: ['create', 'update', 'getAll'] } } },
                { displayName: 'Assigned User ID', name: 'assignedUserId', type: 'number', default: 0, displayOptions: { show: { resource: ['ticket'], operation: ['create', 'update', 'getAll'] } } },
                { displayName: 'Search', name: 'search', type: 'string', default: '', displayOptions: { show: { resource: ['ticket'], operation: ['getAll'] } } },
                { displayName: 'Limit', name: 'limit', type: 'number', default: 50, typeOptions: { minValue: 1, maxValue: 500 }, description: 'Max number of results to return', displayOptions: { show: { resource: ['ticket'], operation: ['getAll'] } } },
                { displayName: 'Raw JSON Body', name: 'rawJsonBody', type: 'json', default: '{}', displayOptions: { show: { operation: ['create', 'update'] } }, description: 'Optional JSON object merged into the request body; useful for advanced MyPortal fields' },
            ],
        };
    }
    async execute() {
        const credentials = await this.getCredentials('myPortalApi');
        const baseUrl = normaliseBaseUrl(credentials.baseUrl);
        const returnData = [];
        for (let i = 0; i < this.getInputData().length; i++) {
            try {
                const resource = this.getNodeParameter('resource', i);
                const operation = this.getNodeParameter('operation', i);
                const path = resource === 'staff' ? '/api/staff' : '/api/tickets';
                const qs = {};
                let method = 'GET';
                let uri = `${baseUrl}${path}${resource === 'ticket' ? '/' : ''}`;
                let body;
                if (operation === 'get' || operation === 'update' || operation === 'delete') {
                    uri = `${baseUrl}${path}/${this.getNodeParameter('id', i)}`;
                }
                if (operation === 'create' || operation === 'update') {
                    method = operation === 'create' ? 'POST' : 'PUT';
                    const raw = parseJsonObject(this.getNodeParameter('rawJsonBody', i, '{}'), 'Raw JSON Body') ?? {};
                    if (resource === 'staff') {
                        body = compactObject({
                            companyId: this.getNodeParameter('companyId', i, 0) || undefined,
                            firstName: this.getNodeParameter('firstName', i, '') || undefined,
                            lastName: this.getNodeParameter('lastName', i, '') || undefined,
                            email: this.getNodeParameter('email', i, '') || undefined,
                            mobilePhone: this.getNodeParameter('mobilePhone', i, '') || undefined,
                            enabled: this.getNodeParameter('enabled', i, true),
                            department: this.getNodeParameter('department', i, '') || undefined,
                            jobTitle: this.getNodeParameter('jobTitle', i, '') || undefined,
                            accountAction: this.getNodeParameter('accountAction', i, '') || undefined,
                            customFields: parseJsonObject(this.getNodeParameter('customFieldsJson', i, '{}'), 'Custom Fields JSON'),
                            ...raw,
                        });
                    }
                    else {
                        body = compactObject({
                            subject: this.getNodeParameter('subject', i, '') || undefined,
                            description: this.getNodeParameter('description', i, '') || undefined,
                            status: this.getNodeParameter('status', i, '') || undefined,
                            priority: this.getNodeParameter('priority', i, '') || undefined,
                            requester_id: this.getNodeParameter('requesterId', i, 0) || undefined,
                            company_id: this.getNodeParameter('ticketCompanyId', i, 0) || undefined,
                            assigned_user_id: this.getNodeParameter('assignedUserId', i, 0) || undefined,
                            ...raw,
                        });
                    }
                }
                else if (operation === 'delete') {
                    method = 'DELETE';
                }
                else if (operation === 'getAll') {
                    if (resource === 'staff') {
                        Object.assign(qs, compactObject({ companyId: this.getNodeParameter('companyId', i, 0) || undefined, email: this.getNodeParameter('email', i, '') || undefined, accountAction: this.getNodeParameter('accountAction', i, '') || undefined }));
                    }
                    else {
                        Object.assign(qs, compactObject({ status: this.getNodeParameter('status', i, '') || undefined, company_id: this.getNodeParameter('ticketCompanyId', i, 0) || undefined, assigned_user_id: this.getNodeParameter('assignedUserId', i, 0) || undefined, search: this.getNodeParameter('search', i, '') || undefined, limit: this.getNodeParameter('limit', i, 50) }));
                    }
                }
                const response = await this.helpers.httpRequestWithAuthentication.call(this, 'myPortalApi', { method, url: uri, qs, body, json: true });
                if (operation === 'delete') {
                    returnData.push({ json: { success: true, id: this.getNodeParameter('id', i) }, pairedItem: { item: i } });
                }
                else if (resource === 'ticket' && operation === 'getAll' && response?.items) {
                    returnData.push(...this.helpers.returnJsonArray(response.items));
                }
                else {
                    returnData.push(...this.helpers.returnJsonArray(Array.isArray(response) ? response : [response]));
                }
            }
            catch (error) {
                if (this.continueOnFail()) {
                    returnData.push({ json: { error: error.message }, pairedItem: { item: i } });
                    continue;
                }
                throw new n8n_workflow_1.NodeOperationError(this.getNode(), error, { itemIndex: i });
            }
        }
        return [returnData];
    }
}
exports.MyPortal = MyPortal;
