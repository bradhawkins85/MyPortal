"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MyPortalApi = void 0;
class MyPortalApi {
    constructor() {
        this.name = 'myPortalApi';
        this.displayName = 'MyPortal API';
        this.icon = 'file:../nodes/MyPortal/myportal.svg';
        this.documentationUrl = 'https://github.com/';
        this.authenticate = {
            type: 'generic',
            properties: {
                headers: { 'x-api-key': '={{$credentials.apiKey}}' },
            },
        };
        this.test = {
            request: {
                baseURL: "={{$credentials.baseUrl.replace(new RegExp('/+$'), '')}}",
                url: '/api/staff',
                method: 'GET',
            },
        };
        this.properties = [
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
}
exports.MyPortalApi = MyPortalApi;
