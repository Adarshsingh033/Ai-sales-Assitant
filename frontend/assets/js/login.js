/**
 * Login page controller.
 *
 * - Redirects authenticated users away immediately.
 * - Calls the real backend API (POST /api/v1/auth/login).
 * - Shows loading state and disables button during request.
 * - Displays user-friendly errors.
 * - Redirects by role on success.
 *
 * NO static credentials. NO hardcoded test emails.
 */

document.addEventListener('DOMContentLoaded', () => {
  // Redirect if already logged in
  Auth.redirectIfAuthenticated();

  const form       = document.getElementById('loginForm');
  const emailInput = document.getElementById('email');
  const passInput  = document.getElementById('password');
  const submitBtn  = document.getElementById('loginBtn');
  const errorAlert = document.getElementById('loginError');
  const passToggle = document.getElementById('passwordToggle');

  // -----------------------------------------------------------------------
  // Password visibility toggle
  // -----------------------------------------------------------------------
  passToggle.addEventListener('click', () => {
    const isPassword = passInput.type === 'password';
    passInput.type = isPassword ? 'text' : 'password';
    passToggle.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
    // Swap icon
    passToggle.querySelector('.icon-eye').style.display     = isPassword ? 'none'  : 'block';
    passToggle.querySelector('.icon-eye-off').style.display = isPassword ? 'block' : 'none';
  });

  // -----------------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------------
  function setLoading(loading) {
    submitBtn.disabled = loading;
    submitBtn.classList.toggle('loading', loading);
  }

  function showError(message) {
    errorAlert.textContent = message;
    errorAlert.classList.add('show');
    errorAlert.classList.remove('alert-success');
    errorAlert.classList.add('alert-error');
  }

  function clearError() {
    errorAlert.classList.remove('show');
    errorAlert.textContent = '';
  }

  function setInputError(input, hasError) {
    input.classList.toggle('error', hasError);
  }

  // -----------------------------------------------------------------------
  // Client-side validation (backend validates independently too)
  // -----------------------------------------------------------------------
  function validate() {
    let valid = true;

    const email    = emailInput.value.trim();
    const password = passInput.value;

    setInputError(emailInput, false);
    setInputError(passInput, false);

    if (!email) {
      setInputError(emailInput, true);
      showError('Email address is required.');
      valid = false;
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setInputError(emailInput, true);
      showError('Please enter a valid email address.');
      valid = false;
    } else if (!password) {
      setInputError(passInput, true);
      showError('Password is required.');
      valid = false;
    }

    return valid;
  }

  // -----------------------------------------------------------------------
  // Form submission
  // -----------------------------------------------------------------------
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearError();

    if (!validate()) return;

    setLoading(true);

    try {
      const response = await Api.login({
        email:    emailInput.value.trim(),
        password: passInput.value,
      });

      // Store session
      Auth.setSession(response);

      // Redirect to role-appropriate dashboard
      Auth.redirectByRole();

    } catch (err) {
      setLoading(false);

      if (err instanceof ApiError) {
        if (err.status === 0) {
          showError('Unable to connect to the server. Please try again.');
        } else if (err.status === 401) {
          showError('Invalid email or password.');
        } else if (err.status === 403) {
          showError('Your account is inactive. Please contact support.');
        } else {
          showError(err.message || 'An unexpected error occurred. Please try again.');
        }
      } else {
        showError('An unexpected error occurred. Please try again.');
      }
    }
  });

  // Clear errors on input change
  [emailInput, passInput].forEach(input => {
    input.addEventListener('input', () => {
      setInputError(input, false);
      clearError();
    });
  });
});
