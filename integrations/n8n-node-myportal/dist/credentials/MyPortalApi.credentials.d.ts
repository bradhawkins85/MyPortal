import type { IAuthenticateGeneric, ICredentialTestRequest, ICredentialType, INodeProperties } from 'n8n-workflow';
export declare class MyPortalApi implements ICredentialType {
    name: string;
    displayName: string;
    icon: "file:../nodes/MyPortal/myportal.svg";
    documentationUrl: string;
    authenticate: IAuthenticateGeneric;
    test: ICredentialTestRequest;
    properties: INodeProperties[];
}
