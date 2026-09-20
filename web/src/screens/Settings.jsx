import AccountSettings from '../components/AccountSettings';

/**
 * `/settings` as a page.
 *
 * The same component the avatar opens as a sheet. A second implementation
 * would be a second place to change one preference, and the two would
 * disagree the first time either was edited.
 */
export default function Settings() {
  return (
    <div className="page">
      <header className="page-head">
        <h1>Settings</h1>
        <p>This account, and how the console looks.</p>
      </header>
      <AccountSettings />
    </div>
  );
}
