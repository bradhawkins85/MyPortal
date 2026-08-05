import type { IAuthenticateGeneric, ICredentialTestRequest, ICredentialType, INodeProperties } from 'n8n-workflow';

export class MyPortalApi implements ICredentialType {
	name = 'myPortalApi';
	displayName = 'MyPortal API';
	icon = 'file:../nodes/MyPortal/myportal.svg' as const;
	documentationUrl = 'https://github.com/';
	authenticate: IAuthenticateGeneric = {
		type: 'generic',
		properties: {
			headers: { 'x-api-key': '={{$credentials.apiKey}}' },
		},
	};

	test: ICredentialTestRequest = {
		request: {
			baseURL: "={{$credentials.baseUrl.replace(new RegExp('/+$'), '')}}",
			url: '/api/staff',
			method: 'GET',
		},
	};

	properties: INodeProperties[] = [
		{
			displayName: 'Base URL',
			name: 'baseUrl',
			type: 'string',
			default: '',
			placeholder: 'https://portal.example.com',
			required: true,
			description: 'Root URL of your MyPortal instance',
		},
		{
			displayName: 'API Key',
			name: 'apiKey',
			type: 'string',
			typeOptions: { password: true },
			default: '',
			required: true,
			description: 'MyPortal API key sent in the x-api-key header',
		},
	];
}
