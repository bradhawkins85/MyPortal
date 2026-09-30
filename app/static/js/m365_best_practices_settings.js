"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const policyControls = document.querySelectorAll(
    '.m365-best-practices-settings input[name="enabled"][data-policy-group]'
  );

  policyControls.forEach((control) => {
    control.addEventListener("change", () => {
      if (!control.checked) return;

      policyControls.forEach((candidate) => {
        if (
          candidate !== control &&
          candidate.dataset.policyGroup === control.dataset.policyGroup
        ) {
          candidate.checked = false;
        }
      });
    });
  });
});
