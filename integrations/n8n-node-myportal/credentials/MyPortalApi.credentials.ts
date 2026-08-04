import type { ICredentialType, INodeProperties } from 'n8n-workflow';

export class MyPortalApi implements ICredentialType {
	name = 'myPortalApi';
	displayName = 'MyPortal API';
	documentationUrl = 'https://github.com/';
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
