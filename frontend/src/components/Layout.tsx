import { Outlet, NavLink } from 'react-router-dom';
import { signOut } from 'aws-amplify/auth';

interface Props {
  onSignOut: () => void;
}

export default function Layout({ onSignOut }: Props) {
  const handleSignOut = async () => {
    await signOut();
    onSignOut();
  };

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      <div className="w-56 bg-white border-r border-gray-200 flex flex-col">
        <div className="p-4 border-b border-gray-200">
          <h1 className="font-bold text-gray-900">Call Analytics</h1>
        </div>
        <nav className="flex-1 p-3 space-y-1">
          <NavLink to="/chat"
            className={({ isActive }) =>
              `flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${isActive ? 'bg-blue-50 text-blue-700' : 'text-gray-600 hover:bg-gray-100'
              }`
            }
          >
            Chat
          </NavLink>
          <NavLink to="/reports"
            className={({ isActive }) =>
              `flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${isActive ? 'bg-blue-50 text-blue-700' : 'text-gray-600 hover:bg-gray-100'
              }`
            }
          >
            Reports
          </NavLink>
        </nav>
        <div className="p-3 border-t border-gray-200">
          <button onClick={handleSignOut}
            className="w-full text-left px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100 transition-colors"
          >
            Sign Out
          </button>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <Outlet />
      </div>
    </div>
  );
}
