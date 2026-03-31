(function () {
  function normalizeFieldType(fieldType) {
    return String(fieldType || 'string').toLowerCase();
  }

  function getSchemaFields(schema) {
    if (!schema || !Array.isArray(schema.fields)) {
      return [];
    }
    return schema.fields
      .map((field) => ({
        name: String(field && field.name ? field.name : '').trim(),
        label: String(field && field.label ? field.label : field && field.name ? field.name : '').trim(),
        type: normalizeFieldType(field && field.type),
        required: Boolean(field && field.required),
        placeholder: field && field.placeholder ? String(field.placeholder) : '',
        enum: Array.isArray(field && field.enum) ? field.enum : null,
      }))
      .filter((field) => field.name);
  }

  function buildSchemaFields(options) {
    const container = options && options.container ? options.container : null;
    const schemaFields = getSchemaFields(options && options.schema);
    const existingPayload = options && options.existingPayload && typeof options.existingPayload === 'object'
      ? options.existingPayload
      : {};
    const onValueChange = options && typeof options.onValueChange === 'function' ? options.onValueChange : null;
    const idPrefix = options && options.idPrefix ? String(options.idPrefix) : 'action-payload';
    if (!container) {
      return { hasSchema: false, fieldNames: new Set() };
    }
    container.textContent = '';
    if (!schemaFields.length) {
      return { hasSchema: false, fieldNames: new Set() };
    }

    schemaFields.forEach((field) => {
      const wrapper = document.createElement('div');
      wrapper.className = 'form-field';

      const label = document.createElement('label');
      label.className = 'form-label';
      label.textContent = field.label || field.name;
      const inputId = `${idPrefix}-${field.name}`;
      label.setAttribute('for', inputId);

      let input;
      if (field.type === 'boolean') {
        input = document.createElement('input');
        input.type = 'checkbox';
        input.className = 'form-checkbox';
        input.checked = Object.prototype.hasOwnProperty.call(existingPayload, field.name)
          ? Boolean(existingPayload[field.name])
          : false;
      } else if (field.type === 'json') {
        input = document.createElement('textarea');
        input.className = 'form-input';
        input.rows = 3;
        const value = existingPayload[field.name];
        input.value = value !== undefined ? JSON.stringify(value, null, 2) : '';
        input.placeholder = field.placeholder || '{}';
      } else if (field.enum) {
        input = document.createElement('select');
        input.className = 'form-input';
        const emptyOption = document.createElement('option');
        emptyOption.value = '';
        emptyOption.textContent = 'Select value';
        input.appendChild(emptyOption);
        field.enum.forEach((enumValue) => {
          const option = document.createElement('option');
          option.value = String(enumValue);
          option.textContent = String(enumValue);
          input.appendChild(option);
        });
        const existing = existingPayload[field.name];
        if (existing !== undefined && existing !== null) {
          input.value = String(existing);
        }
      } else {
        input = document.createElement('input');
        input.type = field.type === 'integer' || field.type === 'number' ? 'number' : 'text';
        input.className = 'form-input';
        const value = existingPayload[field.name];
        input.value = value !== undefined && value !== null ? String(value) : '';
        if (field.placeholder) {
          input.placeholder = field.placeholder;
        }
      }

      input.id = inputId;
      input.dataset.actionSchemaField = 'true';
      input.dataset.fieldName = field.name;
      input.dataset.fieldType = field.type;
      input.dataset.required = field.required ? 'true' : 'false';
      if (onValueChange) {
        input.addEventListener(field.type === 'boolean' ? 'change' : 'input', onValueChange);
      }

      wrapper.appendChild(label);
      wrapper.appendChild(input);
      container.appendChild(wrapper);
    });

    return { hasSchema: true, fieldNames: new Set(schemaFields.map((field) => field.name)) };
  }

  function parseSchemaFields(options) {
    const container = options && options.container ? options.container : null;
    const rowIndex = options && Number.isFinite(options.rowIndex) ? options.rowIndex : 0;
    const prefix = options && options.errorPrefix ? String(options.errorPrefix) : 'Action';
    const payload = {};
    if (!container) {
      return { ok: true, payload };
    }

    const fields = Array.from(container.querySelectorAll('[data-action-schema-field]'));
    for (const field of fields) {
      const fieldName = field.dataset.fieldName || '';
      const fieldType = normalizeFieldType(field.dataset.fieldType);
      const required = field.dataset.required === 'true';
      const rawValue = fieldType === 'boolean' ? String(field.checked) : field.value.trim();

      if (required && !rawValue) {
        return { ok: false, error: `${prefix} ${rowIndex + 1} requires '${fieldName}'.` };
      }
      if (!rawValue && fieldType !== 'boolean') {
        continue;
      }

      if (fieldType === 'integer') {
        const parsed = parseInt(rawValue, 10);
        if (Number.isNaN(parsed)) {
          return { ok: false, error: `${prefix} ${rowIndex + 1} field '${fieldName}' must be an integer.` };
        }
        payload[fieldName] = parsed;
      } else if (fieldType === 'number') {
        const parsed = Number(rawValue);
        if (Number.isNaN(parsed)) {
          return { ok: false, error: `${prefix} ${rowIndex + 1} field '${fieldName}' must be a number.` };
        }
        payload[fieldName] = parsed;
      } else if (fieldType === 'boolean') {
        payload[fieldName] = Boolean(field.checked);
      } else if (fieldType === 'json') {
        try {
          payload[fieldName] = JSON.parse(rawValue);
        } catch (error) {
          return { ok: false, error: `${prefix} ${rowIndex + 1} field '${fieldName}' must be valid JSON.` };
        }
      } else {
        payload[fieldName] = rawValue;
      }
    }

    return { ok: true, payload };
  }

  function parseRawPayload(options) {
    const text = options && typeof options.text === 'string' ? options.text.trim() : '';
    const rowIndex = options && Number.isFinite(options.rowIndex) ? options.rowIndex : 0;
    const prefix = options && options.errorPrefix ? String(options.errorPrefix) : 'Action';
    if (!text) {
      return { ok: true, payload: {} };
    }
    try {
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Invalid payload');
      }
      return { ok: true, payload: parsed };
    } catch (error) {
      return { ok: false, error: `${prefix} ${rowIndex + 1} payload must be valid JSON.` };
    }
  }

  function hasUnknownKeys(payload, schemaFieldNames) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      return false;
    }
    const keys = Object.keys(payload);
    return keys.some((key) => !schemaFieldNames.has(key));
  }

  window.MyPortalActionPayloadEditor = {
    getSchemaFields,
    buildSchemaFields,
    parseSchemaFields,
    parseRawPayload,
    hasUnknownKeys,
  };
})();
