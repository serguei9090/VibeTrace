import React, { useState } from 'react';

export interface UserProfileProps {
  userId: string;
  onUpdate: (name: string) => void;
}

export const UserProfile: React.FC<UserProfileProps> = ({ userId, onUpdate }) => {
  const [name, setName] = useState<string>('Guest');

  const handleClick = () => {
    onUpdate(name);
  };

  return (
    <div className="profile-box">
      <h2>User: {name} ({userId})</h2>
      <button onClick={handleClick}>Save</button>
    </div>
  );
};
