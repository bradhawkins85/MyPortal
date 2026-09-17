(function () {
  function parsePayload(element) {
    var raw = element.getAttribute('data-bcp-payload');
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (error) {
      console.error('Failed to parse BCP action payload.', error);
      return null;
    }
  }

  function valueOrEmpty(value) {
    return value == null ? '' : value;
  }

  function handleAction(element) {
    var action = element.getAttribute('data-bcp-action');
    var payload = parsePayload(element) || {};

    switch (action) {
      case 'edit-contact':
        if (typeof window.editContact === 'function') {
          window.editContact(
            payload.id,
            valueOrEmpty(payload.kind),
            valueOrEmpty(payload.person_or_org),
            valueOrEmpty(payload.phones),
            valueOrEmpty(payload.email),
            valueOrEmpty(payload.responsibility_or_agency)
          );
        }
        break;
      case 'edit-recovery-contact':
        if (typeof window.editContact === 'function') {
          window.editContact(
            payload.id,
            valueOrEmpty(payload.org_name),
            valueOrEmpty(payload.contact_name),
            valueOrEmpty(payload.title),
            valueOrEmpty(payload.phone)
          );
        }
        break;
      case 'edit-risk':
        if (typeof window.editRisk === 'function') {
          window.editRisk(
            payload.id,
            valueOrEmpty(payload.description),
            payload.likelihood,
            payload.impact,
            valueOrEmpty(payload.preventative_actions),
            valueOrEmpty(payload.contingency_plans)
          );
        }
        break;
      case 'edit-role':
        if (typeof window.showEditRoleModal === 'function') {
          window.showEditRoleModal(payload.id, valueOrEmpty(payload.title), payload.responsibilities);
        }
        break;
      case 'assign-role-user':
        if (typeof window.showAssignUserModal === 'function') {
          window.showAssignUserModal(payload.id, valueOrEmpty(payload.title));
        }
        break;
      case 'edit-assignment':
        if (typeof window.showEditAssignmentModal === 'function') {
          window.showEditAssignmentModal(
            payload.id,
            payload.user_id,
            valueOrEmpty(payload.collaborator_role) || 'Executor',
            Boolean(payload.is_alternate),
            valueOrEmpty(payload.contact_info)
          );
        }
        break;
      case 'edit-claim':
        if (typeof window.editClaim === 'function') {
          window.editClaim(
            payload.id,
            valueOrEmpty(payload.insurer),
            valueOrEmpty(payload.claim_date),
            valueOrEmpty(payload.details),
            valueOrEmpty(payload.follow_up_actions)
          );
        }
        break;
      case 'edit-backup':
        if (typeof window.openEditModal === 'function') {
          window.openEditModal(
            payload.id,
            valueOrEmpty(payload.data_scope),
            valueOrEmpty(payload.frequency),
            valueOrEmpty(payload.medium),
            valueOrEmpty(payload.owner),
            valueOrEmpty(payload.steps),
            Boolean(payload.is_job_linked)
          );
        }
        break;
      case 'edit-policy':
        if (typeof window.openEditModal === 'function') {
          window.openEditModal(
            payload.id,
            valueOrEmpty(payload.type),
            valueOrEmpty(payload.coverage),
            valueOrEmpty(payload.exclusions),
            valueOrEmpty(payload.insurer),
            valueOrEmpty(payload.contact),
            valueOrEmpty(payload.last_review_date),
            valueOrEmpty(payload.payment_terms)
          );
        }
        break;
      case 'edit-emergency-item':
        if (typeof window.openEditModal === 'function') {
          window.openEditModal(
            payload.id,
            valueOrEmpty(payload.category),
            valueOrEmpty(payload.name),
            valueOrEmpty(payload.notes)
          );
        }
        break;
      case 'edit-recovery-action':
        if (typeof window.showEditModal === 'function') {
          window.showEditModal(
            payload.id,
            valueOrEmpty(payload.action),
            valueOrEmpty(payload.resources),
            payload.owner_id,
            payload.rto_hours,
            valueOrEmpty(payload.due_date),
            payload.critical_activity_id
          );
        }
        break;
      case 'edit-market-change':
        if (typeof window.editChange === 'function') {
          window.editChange(
            payload.id,
            valueOrEmpty(payload.change),
            valueOrEmpty(payload.impact),
            valueOrEmpty(payload.options)
          );
        }
        break;
      case 'edit-training':
        if (typeof window.editTraining === 'function') {
          window.editTraining(
            payload.id,
            valueOrEmpty(payload.training_date),
            valueOrEmpty(payload.training_type),
            valueOrEmpty(payload.status),
            payload.participants_count,
            payload.score_percent,
            valueOrEmpty(payload.comments),
            valueOrEmpty(payload.lessons_learned),
            valueOrEmpty(payload.follow_up_actions)
          );
        }
        break;
      case 'edit-review':
        if (typeof window.editReview === 'function') {
          window.editReview(
            payload.id,
            valueOrEmpty(payload.review_date),
            valueOrEmpty(payload.version_label),
            valueOrEmpty(payload.approval_status),
            payload.reviewed_by_user_id,
            payload.approved_by_user_id,
            valueOrEmpty(payload.reason),
            valueOrEmpty(payload.changes_made),
            valueOrEmpty(payload.approval_snapshot)
          );
        }
        break;
      default:
        break;
    }
  }

  document.addEventListener('click', function (event) {
    var element = event.target.closest('[data-bcp-action]');
    if (!element || element.disabled) return;
    event.preventDefault();
    handleAction(element);
  });
}());
